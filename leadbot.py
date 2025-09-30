# lead_automation_tool_full.py

import os
import sys
import time
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import pandas as pd
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.chrome.options import Options as ChromeOptions
from openai import OpenAI
try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False
    TavilyClient = None

# -------------------------
# Helper Functions
# -------------------------

actions_log = []
PREFS_FILE = os.path.join('configs', 'last_session.json')
LLM_CONFIG_FILE = os.path.join('configs', 'llm_config.json')

# -------------------------
# LLM Integration
# -------------------------

def load_llm_config():
    """Load LLM configuration (API key, model, etc.)"""
    try:
        if os.path.exists(LLM_CONFIG_FILE):
            with open(LLM_CONFIG_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {
        'enabled': False,
        'api_key': '',
        'model': 'gpt-4o-mini',
        'base_url': None,  # Optional for custom endpoints
        'enable_search': False,
        'search_api_key': ''  # Tavily API key for web research
    }

def save_llm_config(config):
    """Save LLM configuration."""
    try:
        os.makedirs('configs', exist_ok=True)
        with open(LLM_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving LLM config: {e}")

def perform_web_research(query, search_api_key):
    """
    Perform web search using Tavily API to gather real-time information.
    
    Args:
        query: search query string
        search_api_key: Tavily API key
    
    Returns:
        str: summarized search results, or None if error
    """
    if not TAVILY_AVAILABLE or not search_api_key:
        return None
    
    try:
        client = TavilyClient(api_key=search_api_key)
        response = client.search(query, max_results=3)
        
        # Extract and format results
        results = []
        for item in response.get('results', []):
            title = item.get('title', '')
            content = item.get('content', '')
            if title and content:
                results.append(f"{title}: {content[:200]}")
        
        return "\n\n".join(results) if results else None
    except Exception as e:
        print(f"Web research error: {e}")
        return None

def infer_field_value_with_llm(field_context, csv_row_data, available_options=None):
    """
    Use LLM to intelligently infer what value should be entered into a field
    based on the CSV row data and field context.
    Optionally performs web research to find accurate information.
    
    Args:
        field_context: dict with 'id', 'name', 'type', 'tag', etc.
        csv_row_data: dict of CSV column -> value for this row
        available_options: list of options if it's a select field
    
    Returns:
        str: suggested value, or None if LLM disabled or error
    """
    config = load_llm_config()
    if not config.get('enabled') or not config.get('api_key'):
        return None
    
    try:
        # Build prompt
        field_id = field_context.get('id', 'unknown')
        field_type = field_context.get('type', 'text')
        field_tag = field_context.get('tag', 'input')
        
        # Perform web research if enabled
        research_results = None
        if config.get('enable_search') and config.get('search_api_key'):
            # Build search query from CSV data
            search_terms = []
            for col, val in csv_row_data.items():
                if val and str(val).strip() and col.lower() in ['name', 'company', 'business', 'organization']:
                    search_terms.append(str(val))
            
            if search_terms:
                # Add field context to search
                if field_id and field_id != 'unknown':
                    search_query = f"{' '.join(search_terms)} {field_id}"
                else:
                    search_query = ' '.join(search_terms)
                
                print(f"    Researching: {search_query}")
                research_results = perform_web_research(search_query, config['search_api_key'])
        
        prompt = f"""You are a form-filling assistant. Based on the provided business data and any research results, suggest the most appropriate value for the form field.

Form Field:
- Field ID: {field_id}
- Field Type: {field_type}
- Field Tag: {field_tag}

Business Data:
"""
        for col, val in csv_row_data.items():
            prompt += f"- {col}: {val}\n"
        
        if research_results:
            prompt += f"\n=== Web Research Results ===\n{research_results}\n\n"
        
        if available_options:
            prompt += f"\nAvailable Options (select one):\n"
            for opt in available_options:
                prompt += f"- {opt}\n"
            prompt += "\nRespond with ONLY the exact option text that best matches the business data and research."
        else:
            prompt += "\nRespond with ONLY the value to enter (no explanation). Be concise and accurate."
        
        # Call OpenAI API
        client = OpenAI(api_key=config['api_key'], base_url=config.get('base_url'))
        response = client.chat.completions.create(
            model=config.get('model', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": "You are a helpful form-filling assistant with access to web research. Respond with only the requested value based on the data and research provided, no explanations."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=100
        )
        
        suggested_value = response.choices[0].message.content.strip()
        return suggested_value
    
    except Exception as e:
        print(f"LLM inference error: {e}")
        return None

def init_driver(headless=False, parent=None):
    """
    Initialize a Selenium WebDriver with Chrome or Edge.
    - Automatically detects installed browsers.
    - If multiple locations found, lets user select.
    - Provides clear errors if none found.
    """
    import os
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.microsoft import EdgeChromiumDriverManager

    # Helper to detect installed browser binaries
    def detect_browsers():
        chrome_paths = [
            os.path.expandvars(r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"),
            os.path.expandvars(r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"),
            os.path.expandvars(r"C:\\Users\\%USERNAME%\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe")
        ]
        edge_paths = [
            os.path.expandvars(r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"),
            os.path.expandvars(r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"),
            os.path.expandvars(r"C:\\Users\\%USERNAME%\\AppData\\Local\\Microsoft\\Edge\\Application\\msedge.exe")
        ]
        installed = {'chrome': [], 'edge': []}
        for p in chrome_paths:
            if os.path.exists(p):
                installed['chrome'].append(p)
        for p in edge_paths:
            if os.path.exists(p):
                installed['edge'].append(p)
        return installed

    installed = detect_browsers()

    # Ask user to pick if multiple
    browser_choice = None
    binary_path = None
    if installed['chrome']:
        if len(installed['chrome']) == 1:
            browser_choice = 'chrome'
            binary_path = installed['chrome'][0]
        else:
            browser_choice = 'chrome'
            binary_path = simpledialog.askstring(
                "Select Chrome",
                f"Multiple Chrome installations detected:\n{installed['chrome']}\nEnter full path to use:",
                parent=parent
            )
    elif installed['edge']:
        if len(installed['edge']) == 1:
            browser_choice = 'edge'
            binary_path = installed['edge'][0]
        else:
            browser_choice = 'edge'
            binary_path = simpledialog.askstring(
                "Select Edge",
                f"Multiple Edge installations detected:\n{installed['edge']}\nEnter full path to use:",
                parent=parent
            )
    else:
        messagebox.showerror(
            "Browser Not Found",
            "No Chrome or Edge installations detected. Please install Google Chrome or Microsoft Edge."
        )
        raise RuntimeError("No Chromium-based browser found.")

    # Set options
    if browser_choice == 'chrome':
        options = ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        if binary_path:
            options.binary_location = binary_path
        try:
            # Check for local chromedriver in common locations
            local_driver = os.environ.get("CHROME_DRIVER_PATH")
            if not local_driver:
                candidates = [
                    os.path.join(os.getcwd(), "drivers", "chromedriver.exe"),
                    os.path.join(os.getcwd(), "drivers", "chromedriver_win64", "chromedriver.exe"),
                ]
                for c in candidates:
                    if os.path.exists(c):
                        local_driver = c
                        break
            
            if local_driver and os.path.exists(local_driver):
                return webdriver.Chrome(service=Service(executable_path=local_driver), options=options)
            else:
                return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        except Exception as e:
            raise RuntimeError(f"Failed to start Chrome driver: {e}")
    elif browser_choice == 'edge':
        options = EdgeOptions()
        if headless:
            options.add_argument("--headless=new")
        if binary_path:
            options.binary_location = binary_path
        try:
            # Check for local msedgedriver in common locations
            local_driver = os.environ.get("EDGE_DRIVER_PATH")
            if not local_driver:
                candidates = [
                    os.path.join(os.getcwd(), "drivers", "msedgedriver.exe"),
                    os.path.join(os.getcwd(), "drivers", "edgedriver_win64", "msedgedriver.exe"),
                ]
                for c in candidates:
                    if os.path.exists(c):
                        local_driver = c
                        break
            
            if local_driver and os.path.exists(local_driver):
                return webdriver.Edge(service=EdgeService(executable_path=local_driver), options=options)
            else:
                return webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()), options=options)
        except Exception as e:
            raise RuntimeError(f"Failed to start Edge driver: {e}")

def detect_dynamic_fields(driver):
    """Wait for fields to render, then extract them from the page."""
    # Wait up to 10 seconds for at least one input/select/textarea to appear
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input, select, textarea"))
        )
    except Exception:
        # If timeout, proceed anyway - page might not have forms
        pass
    
    # Give a moment for any additional fields to render
    time.sleep(1)
    
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    fields = {}
    for i, input_tag in enumerate(soup.find_all(['input', 'select', 'textarea']), start=1):
        field_info = {
            'tag': input_tag.name,
            'id': input_tag.get('id'),
            'name': input_tag.get('name'),
            'type': input_tag.get('type'),
            'placeholder': input_tag.get('placeholder'),
        }
        fields[f'field_{i}'] = field_info
    return fields

# -------------------------
# Recording Helpers (JS Injection)
# -------------------------

def inject_recorder(driver):
    """Inject JavaScript into the page to record user actions into window._lgEvents."""
    script = r"""
    (function(){
      try {
        if (window._lgRecorderInstalled) return true;
        window._lgRecorderInstalled = true;
        window._lgEvents = [];
        function cssPath(el){
          if (!(el instanceof Element)) return '';
          var path = [];
          while (el && el.nodeType === Node.ELEMENT_NODE){
            var selector = el.nodeName.toLowerCase();
            if (el.id){ selector += '#' + el.id; path.unshift(selector); break; }
            else {
              var sib = el, nth = 1;
              while (sib = sib.previousElementSibling){ if (sib.nodeName.toLowerCase() === el.nodeName.toLowerCase()) nth++; }
              selector += ':nth-of-type(' + nth + ')';
            }
            path.unshift(selector);
            el = el.parentNode;
          }
          return path.join(' > ');
        }
        function record(evt){
          var t = evt.target; if (!t) return;
          var tag = (t.tagName||'').toLowerCase();
          if (['input','select','textarea','button','a'].indexOf(tag) === -1 && evt.type==='click'){
            var closest = t.closest && t.closest('input,select,textarea,button,a');
            if (closest) { t = closest; tag = t.tagName.toLowerCase(); }
          }
          var entry = {
            eventType: evt.type,
            tag: tag,
            id: t.id || null,
            name: t.name || null,
            typeAttr: t.type || null,
            value: (evt.type === 'input' || evt.type === 'change') ? (t.value || '') : null,
            cssPath: cssPath(t),
            ts: Date.now()
          };
          window._lgEvents.push(entry);
        }
        ['click','input','change'].forEach(function(type){ document.addEventListener(type, record, true); });
        window._lgDrain = function(){ var r = window._lgEvents.slice(); window._lgEvents.length = 0; return r; };
        return true;
      } catch(e) { return false; }
    })();
    """
    try:
        driver.execute_script(script)
    except Exception:
        pass

def build_action_from_event(ev):
    """Map a recorded JS event to our action schema."""
    # Prefer ID, then NAME, else CSS
    if ev.get('id'):
        by, selector = 'ID', ev['id']
    elif ev.get('name'):
        by, selector = 'NAME', ev['name']
    else:
        css = ev.get('cssPath') or ''
        if not css:
            return None
        by, selector = 'CSS_SELECTOR', css

    et = ev.get('eventType')
    tag = (ev.get('tag') or '').lower()
    if et == 'click':
        action = 'click'
    elif et in ('input','change'):
        if tag == 'select':
            action = 'select'
        else:
            action = 'input'
    else:
        return None

    act = {'action': action, 'by': by, 'selector': selector}
    if action in ('input','select') and ev.get('value') is not None:
        act['value'] = str(ev.get('value'))
    
    # Add field context for better UI display
    act['field_context'] = {
        'tag': tag,
        'type': ev.get('typeAttr'),
        'id': ev.get('id'),
        'name': ev.get('name'),
        'placeholder': None,  # Will be enriched later
        'label': None  # Will be enriched later
    }
    return act

def deduplicate_actions(actions):
    """Keep only the final input/select value for each field, preserve clicks and order."""
    # Track last seen input/select for each selector
    last_input = {}
    result = []
    
    for act in actions:
        key = (act.get('by'), act.get('selector'))
        action_type = act.get('action')
        
        if action_type in ('input', 'select'):
            # Update the last value for this field
            last_input[key] = act
        elif action_type == 'click':
            # Flush any pending inputs before this click
            for k in sorted(last_input.keys()):
                if last_input[k] not in result:
                    result.append(last_input[k])
            last_input.clear()
            result.append(act)
    
    # Flush remaining inputs at the end
    for k in sorted(last_input.keys()):
        if last_input[k] not in result:
            result.append(last_input[k])
    
    return result

def detect_fields_via_requests(url: str, timeout: int = 20):
    """
    Fetch the page using HTTP and parse form fields without launching a browser.
    This avoids requiring Chrome/Edge and any driver downloads.
    Note: JavaScript-rendered fields won't appear with this method.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36'
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"HTTP request failed: {e}")
    
    html = resp.text
    soup = BeautifulSoup(html, 'html.parser')
    fields = {}
    for i, input_tag in enumerate(soup.find_all(['input', 'select', 'textarea']), start=1):
        field_info = {
            'tag': input_tag.name,
            'id': input_tag.get('id'),
            'name': input_tag.get('name'),
            'type': input_tag.get('type'),
            'placeholder': input_tag.get('placeholder'),
        }
        fields[f'field_{i}'] = field_info
    
    # Provide debug info if no fields found
    if not fields:
        form_count = len(soup.find_all('form'))
        raise RuntimeError(
            f"No input/select/textarea fields found on page. "
            f"Forms found: {form_count}. "
            f"Page may require JavaScript or login. Try using a browser to access it manually first."
        )
    return fields

# -------------------------
# Action Wrappers
# -------------------------

def click_element(driver, by, selector):
    el = driver.find_element(by, selector)
    el.click()
    actions_log.append({'action': 'click', 'by': by, 'selector': selector})

def fill_input(driver, by, selector, value):
    el = driver.find_element(by, selector)
    el.clear()
    el.send_keys(value)
    actions_log.append({'action': 'input', 'by': by, 'selector': selector, 'value': value})

def select_dropdown(driver, by, selector, value):
    from selenium.webdriver.support.ui import Select
    el = Select(driver.find_element(by, selector))
    el.select_by_visible_text(value)
    actions_log.append({'action': 'select', 'by': by, 'selector': selector, 'value': value})

# -------------------------
# CSV Mapping
# -------------------------

class CSVMappingWindow:
    """GUI window for mapping CSV columns to workflow actions."""
    def __init__(self, parent, csv_file, actions, existing_mapping=None):
        self.result_mapping = {}
        self.cancelled = False
        self.existing_mapping = existing_mapping or {}
        
        # Load CSV
        self.df = pd.read_csv(csv_file)
        self.columns = ['(skip)', '(use recorded value)'] + self.df.columns.tolist()
        
        # Show ALL actions for context, not just input/select
        self.all_actions = actions
        self.input_actions = [a for a in actions if a.get('action') in ('input', 'select')]
        
        # Create window
        self.window = tk.Toplevel(parent)
        self.window.title("CSV Mapping")
        self.window.geometry("900x600")
        
        # Title
        ttk.Label(self.window, text="Map CSV Columns to Workflow Fields", font=('Arial', 14, 'bold')).pack(pady=10)
        ttk.Label(self.window, text="All workflow steps shown below. Map CSV columns to input/select fields.", font=('Arial', 10)).pack(pady=5)
        ttk.Label(self.window, text="Clicks/navigation shown for context (to identify phantom steps)", font=('Arial', 9, 'italic'), foreground='gray').pack(pady=2)
        
        # Create scrollable frame
        canvas = tk.Canvas(self.window)
        scrollbar = ttk.Scrollbar(self.window, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Table headers
        header_frame = ttk.Frame(scrollable_frame)
        header_frame.pack(fill='x', padx=10, pady=5)
        ttk.Label(header_frame, text="Step", width=6, font=('Arial', 10, 'bold')).grid(row=0, column=0, padx=5)
        ttk.Label(header_frame, text="Field Info", width=40, font=('Arial', 10, 'bold')).grid(row=0, column=1, padx=5, sticky='w')
        ttk.Label(header_frame, text="CSV Column", width=25, font=('Arial', 10, 'bold')).grid(row=0, column=2, padx=5)
        
        # Store comboboxes
        self.comboboxes = {}
        
        # Create row for EACH action (all types)
        for i, action in enumerate(self.all_actions, 1):
            action_type = action.get('action', 'unknown')
            row_frame = ttk.Frame(scrollable_frame, relief='solid', borderwidth=1)
            row_frame.pack(fill='x', padx=10, pady=2)
            
            # Step number
            ttk.Label(row_frame, text=f"{i}", width=6).grid(row=0, column=0, padx=5, pady=5)
            
            # Field info
            field_info = self._build_field_info(action)
            
            # For clicks/navigates: show as context only (gray, no dropdown)
            if action_type in ('click', 'navigate'):
                info_label = ttk.Label(row_frame, text=field_info, width=70, wraplength=500, justify='left', foreground='gray')
                info_label.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky='w')
            else:
                # For input/select: show with mapping dropdown
                info_label = ttk.Label(row_frame, text=field_info, width=40, wraplength=300, justify='left')
                info_label.grid(row=0, column=1, padx=5, pady=5, sticky='w')
                
                # CSV column selector
                combo = ttk.Combobox(row_frame, values=self.columns, state='readonly', width=25)
                
                # Pre-populate with existing mapping if available
                selector = action.get('selector')
                if selector in self.existing_mapping:
                    existing_value = self.existing_mapping[selector]
                    if existing_value == '__RECORDED__':
                        combo.set('(use recorded value)')
                    elif existing_value in self.columns:
                        combo.set(existing_value)
                    else:
                        combo.set('(skip)')
                else:
                    combo.set('(skip)')
                
                combo.grid(row=0, column=2, padx=5, pady=5)
                
                # Store for later retrieval
                self.comboboxes[selector] = combo
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Buttons
        btn_frame = ttk.Frame(self.window)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Save Mapping", command=self._on_save).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side='left', padx=5)
        
        # Make modal
        self.window.transient(parent)
        self.window.grab_set()
        parent.wait_window(self.window)
    
    def _build_field_info(self, action):
        """Build display text for field."""
        action_type = action.get('action', 'unknown').upper()
        by_type = action.get('by', 'unknown')
        selector = action.get('selector', '')
        value = action.get('value', '')
        
        context = action.get('field_context', {})
        field_id = context.get('id') or ''
        field_type = context.get('type') or ''
        
        # Build info string
        parts = [f"{action_type}"]
        
        # Special handling for clicks and navigates
        if action_type == 'CLICK':
            parts.append(f"Element: {selector[:50]}..." if len(selector) > 50 else f"Element: {selector}")
            return " | ".join(parts) + " [Context only - not mappable]"
        elif action_type == 'NAVIGATE':
            url = action.get('url', 'unknown')
            parts.append(f"URL: {url[:60]}..." if len(url) > 60 else f"URL: {url}")
            return " | ".join(parts) + " [Context only - not mappable]"
        
        # For input/select fields
        if field_id:
            parts.append(f"ID: {field_id}")
        elif by_type == 'NAME':
            parts.append(f"Name: {selector}")
        elif field_type:
            parts.append(f"Type: {field_type}")
        
        if value and action_type == 'INPUT':
            val_preview = value[:25] + ('...' if len(value) > 25 else '')
            parts.append(f"Value: '{val_preview}'")
        elif value and action_type == 'SELECT':
            parts.append(f"Selected: '{value}'")
        
        return " | ".join(parts)
    
    def _on_save(self):
        """Save mapping and close."""
        for selector, combo in self.comboboxes.items():
            col = combo.get()
            if col and col not in ['(skip)', '(use recorded value)']:
                # Map to CSV column
                self.result_mapping[selector] = col
            elif col == '(use recorded value)':
                # Special marker to use recorded value
                self.result_mapping[selector] = '__RECORDED__'
            # If '(skip)', don't add to mapping
        self.window.destroy()
    
    def _on_cancel(self):
        """Cancel and close."""
        self.cancelled = True
        self.window.destroy()

def map_csv_to_actions(csv_file, actions, existing_mapping=None, driver=None, parent=None):
    """Open GUI window for CSV mapping."""
    mapper = CSVMappingWindow(parent, csv_file, actions, existing_mapping)
    if mapper.cancelled:
        return {}, []
    
    mapping = mapper.result_mapping
    # Find unmapped selectors
    input_selectors = [a.get('selector') for a in actions if a.get('action') in ('input', 'select')]
    unmapped = [s for s in input_selectors if s not in mapping or not mapping[s]]
    return mapping, unmapped

# -------------------------
# Workflow Editor Dialog
# -------------------------

class WorkflowEditorDialog:
    """Dialog to edit workflow CSV mappings and settings."""
    def __init__(self, parent, config, csv_file, main_log_func=None):
        self.config = config
        self.csv_file = csv_file
        self.main_log = main_log_func
        self.result_config = None
        
        # Load CSV columns
        df = pd.read_csv(csv_file)
        self.csv_columns = ['(skip)', '(use recorded value)'] + df.columns.tolist()
        
        # Create window
        self.window = tk.Toplevel(parent)
        self.window.title("Edit Workflow")
        self.window.geometry("900x700")
        
        # Title
        ttk.Label(self.window, text="Edit Workflow Settings", font=('Arial', 14, 'bold')).pack(pady=10)
        
        # Loop start point
        loop_frame = ttk.LabelFrame(self.window, text="Loop Settings", padding=10)
        loop_frame.pack(fill='x', padx=10, pady=5)
        
        ttk.Label(loop_frame, text="Start from step (for 2nd+ iterations):").pack(side='left', padx=5)
        self.loop_start_var = tk.IntVar(value=config.get('loop_start_step', 0))
        loop_spin = ttk.Spinbox(loop_frame, from_=0, to=len(config.get('actions', [])), textvariable=self.loop_start_var, width=10)
        loop_spin.pack(side='left', padx=5)
        ttk.Label(loop_frame, text="(0 = start from beginning every time)", foreground='gray').pack(side='left', padx=5)
        
        # CSV Mappings
        mapping_frame = ttk.LabelFrame(self.window, text="CSV Column Mappings", padding=10)
        mapping_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        # Scrollable area
        canvas = tk.Canvas(mapping_frame)
        scrollbar = ttk.Scrollbar(mapping_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Headers
        header_frame = ttk.Frame(scrollable_frame)
        header_frame.pack(fill='x', padx=5, pady=5)
        ttk.Label(header_frame, text="Step", width=6, font=('Arial', 10, 'bold')).grid(row=0, column=0, padx=5)
        ttk.Label(header_frame, text="Action", width=15, font=('Arial', 10, 'bold')).grid(row=0, column=1, padx=5)
        ttk.Label(header_frame, text="Field/Name", width=30, font=('Arial', 10, 'bold')).grid(row=0, column=2, padx=5)
        ttk.Label(header_frame, text="CSV Column", width=25, font=('Arial', 10, 'bold')).grid(row=0, column=3, padx=5)
        
        # Store comboboxes
        self.mapping_combos = {}
        
        # Create row for each action
        actions = config.get('actions', [])
        csv_mapping = config.get('csv_mapping', {})
        
        for i, action in enumerate(actions, 1):
            action_type = action.get('action', 'unknown')
            selector = action.get('selector', '')
            step_name = action.get('step_name', '')
            
            row_frame = ttk.Frame(scrollable_frame, relief='solid', borderwidth=1)
            row_frame.pack(fill='x', padx=5, pady=2)
            
            # Step number
            ttk.Label(row_frame, text=f"{i}", width=6).grid(row=0, column=0, padx=5, pady=5)
            
            # Action type
            ttk.Label(row_frame, text=action_type.upper(), width=15).grid(row=0, column=1, padx=5, pady=5)
            
            # Field info - prioritize step name
            if step_name:
                field_text = step_name
            elif action_type in ('input', 'select'):
                field_id = action.get('field_context', {}).get('id', '')
                field_text = field_id if field_id else selector[:40]
            else:
                field_text = selector[:40]
            
            ttk.Label(row_frame, text=field_text, width=30, wraplength=200).grid(row=0, column=2, padx=5, pady=5)
            
            # CSV mapping dropdown (only for input/select)
            if action_type in ('input', 'select'):
                combo = ttk.Combobox(row_frame, values=self.csv_columns, state='readonly', width=23)
                
                # Set current mapping
                current_mapping = csv_mapping.get(selector, '(skip)')
                if current_mapping == '__RECORDED__':
                    combo.set('(use recorded value)')
                elif current_mapping in self.csv_columns:
                    combo.set(current_mapping)
                else:
                    combo.set('(skip)')
                
                combo.grid(row=0, column=3, padx=5, pady=5)
                self.mapping_combos[selector] = combo
            else:
                ttk.Label(row_frame, text="(not mappable)", foreground='gray', width=23).grid(row=0, column=3, padx=5, pady=5)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Buttons
        btn_frame = ttk.Frame(self.window)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Save Changes", command=self.save_changes).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.cancel).pack(side='left', padx=5)
        
        self.window.transient(parent)
        self.window.grab_set()
        self.window.wait_window()
    
    def save_changes(self):
        """Save updated config."""
        # Update CSV mapping
        new_mapping = {}
        for selector, combo in self.mapping_combos.items():
            value = combo.get()
            if value == '(use recorded value)':
                new_mapping[selector] = '__RECORDED__'
            elif value != '(skip)':
                new_mapping[selector] = value
        
        # Update config
        self.config['csv_mapping'] = new_mapping
        self.config['loop_start_step'] = self.loop_start_var.get()
        
        self.result_config = self.config
        
        if self.main_log:
            self.main_log(f"[EDIT] Updated CSV mappings: {len(new_mapping)} fields mapped")
            self.main_log(f"[EDIT] Loop start step: {self.loop_start_var.get()}")
        
        self.window.destroy()
    
    def cancel(self):
        """Cancel without saving."""
        self.result_config = None
        self.window.destroy()
    
    def get_result(self):
        """Return updated config or None if cancelled."""
        return self.result_config

# -------------------------
# Workflow Verification Dialog
# -------------------------

class VerificationDialog:
    """Step-by-step workflow verification dialog."""
    def __init__(self, parent, config, csv_file, main_log_func=None):
        self.parent = parent
        self.config = config
        self.csv_file = csv_file
        self.driver = None
        self.current_step = 0
        self.verified_actions = []
        self.deleted_steps = []  # Track deleted steps for restore
        self.approved = False
        self.main_log = main_log_func  # Log to main app output
        
        # Load first row of CSV for testing
        df = pd.read_csv(csv_file)
        if len(df) == 0:
            raise ValueError("CSV file is empty")
        self.test_row = df.iloc[0].to_dict()
        
        # Create verification window
        self.window = tk.Toplevel(parent)
        self.window.title("Verify Workflow")
        self.window.geometry("600x500")
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Title
        ttk.Label(self.window, text="Workflow Verification", font=('Arial', 14, 'bold')).pack(pady=10)
        ttk.Label(self.window, text="Review each step before running on all CSV rows", font=('Arial', 10)).pack(pady=5)
        
        # Previous step (for context)
        self.prev_frame = ttk.LabelFrame(self.window, text="Previous Verified Step", padding=5)
        self.prev_frame.pack(fill='x', padx=10, pady=5)
        self.prev_label = ttk.Label(self.prev_frame, text="None yet", wraplength=550, justify='left', foreground='gray')
        self.prev_label.pack(anchor='w')
        
        # CSV data preview
        self.csv_frame = ttk.LabelFrame(self.window, text="CSV Row Data (for reference)", padding=5)
        self.csv_frame.pack(fill='x', padx=10, pady=5)
        csv_preview = ", ".join([f"{k}: {str(v)[:30]}" for k, v in list(self.test_row.items())[:4]])
        if len(self.test_row) > 4:
            csv_preview += "..."
        ttk.Label(self.csv_frame, text=csv_preview, wraplength=550, justify='left', foreground='blue', font=('Arial', 9)).pack(anchor='w')
        
        # Current step info frame
        self.info_frame = ttk.LabelFrame(self.window, text="Current Step", padding=10)
        self.info_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.step_label = ttk.Label(self.info_frame, text="", font=('Arial', 10, 'bold'))
        self.step_label.pack(anchor='w', pady=5)
        
        self.action_label = ttk.Label(self.info_frame, text="", wraplength=550, justify='left')
        self.action_label.pack(anchor='w', pady=5)
        
        self.data_label = ttk.Label(self.info_frame, text="", wraplength=550, justify='left', foreground='blue')
        self.data_label.pack(anchor='w', pady=5)
        
        self.value_label = ttk.Label(self.info_frame, text="", wraplength=550, justify='left', font=('Arial', 11, 'bold'), foreground='green')
        self.value_label.pack(anchor='w', pady=5)
        
        # LLM reasoning (if applicable)
        self.reasoning_text = tk.Text(self.info_frame, height=6, width=70, wrap='word', state='disabled')
        self.reasoning_text.pack(fill='both', expand=True, pady=5)
        
        # Value override
        override_frame = ttk.Frame(self.info_frame)
        override_frame.pack(fill='x', pady=5)
        ttk.Label(override_frame, text="Override value:").pack(side='left', padx=5)
        self.override_var = tk.StringVar()
        self.override_entry = ttk.Entry(override_frame, textvariable=self.override_var, width=40)
        self.override_entry.pack(side='left', fill='x', expand=True, padx=5)
        
        # Step naming
        name_frame = ttk.Frame(self.info_frame)
        name_frame.pack(fill='x', pady=5)
        ttk.Label(name_frame, text="Step Name (optional):").pack(side='left', padx=5)
        self.step_name_var = tk.StringVar()
        self.step_name_entry = ttk.Entry(name_frame, textvariable=self.step_name_var, width=40)
        self.step_name_entry.pack(side='left', fill='x', expand=True, padx=5)
        
        # Element override
        element_frame = ttk.Frame(self.info_frame)
        element_frame.pack(fill='x', pady=5)
        ttk.Button(element_frame, text="🎯 Override Element (Click on Page)", command=self.override_element).pack(side='left', padx=5)
        self.element_status = ttk.Label(element_frame, text="", foreground='blue')
        self.element_status.pack(side='left', padx=5)
        
        # Buttons
        btn_frame = ttk.Frame(self.window)
        btn_frame.pack(pady=10)
        
        self.btn_previous = ttk.Button(btn_frame, text="← Previous Step", command=self.go_previous)
        self.btn_previous.pack(side='left', padx=5)
        
        self.btn_approve = ttk.Button(btn_frame, text="✓ Approve & Next", command=self.approve_step)
        self.btn_approve.pack(side='left', padx=5)
        
        self.btn_delete = ttk.Button(btn_frame, text="✗ Delete Step", command=self.delete_step)
        self.btn_delete.pack(side='left', padx=5)
        
        self.btn_restore = ttk.Button(btn_frame, text="↺ Restore Deleted", command=self.restore_deleted)
        self.btn_restore.pack(side='left', padx=5)
        
        self.btn_skip = ttk.Button(btn_frame, text="Skip (Don't Execute)", command=self.skip_step)
        self.btn_skip.pack(side='left', padx=5)
        
        self.btn_cancel = ttk.Button(btn_frame, text="Cancel Verification", command=self.on_close)
        self.btn_cancel.pack(side='left', padx=5)
        
        # Status
        self.status_label = ttk.Label(self.window, text="", foreground='gray')
        self.status_label.pack(pady=5)
        
        # Make modal
        self.window.transient(parent)
        self.window.grab_set()
    
    def start_verification(self):
        """Initialize browser and start stepping through workflow."""
        try:
            self.log("Initializing browser for verification...")
            # Initialize browser
            self.driver = init_driver(headless=False, parent=self.parent)
            self.driver.get(self.config['url'])
            time.sleep(1)
            self.log(f"Browser opened: {self.config['url']}")
            
            # Show first step
            self.show_step()
            
            # Make window modal
            self.window.wait_window()
            
            return self.approved, self.verified_actions
        except Exception as e:
            error_msg = f"Could not start verification: {e}"
            import traceback
            full_trace = traceback.format_exc()
            self.log(f"ERROR: {error_msg}")
            if self.main_log:
                self.main_log(f"[VERIFY ERROR] {error_msg}\n{full_trace}")
            messagebox.showerror("Verification Failed", error_msg)
            if self.driver:
                self.driver.quit()
            return False, []
    
    def show_step(self):
        """Display current step information."""
        if self.current_step >= len(self.config['actions']):
            self.complete_verification()
            return
        
        # Update previous step display
        if self.verified_actions:
            last_action = self.verified_actions[-1]
            last_type = last_action.get('action', 'unknown').upper()
            last_selector = last_action.get('selector', 'N/A')[:50]
            last_value = last_action.get('value', '')
            prev_text = f"{last_type}"
            if last_value:
                prev_text += f" | Value: {last_value[:30]}"
            if last_selector:
                prev_text += f" | {last_selector}..."
            self.prev_label.config(text=prev_text, foreground='green')
        else:
            self.prev_label.config(text="None yet", foreground='gray')
        
        action = self.config['actions'][self.current_step]
        action_type = action.get('action', 'unknown')
        
        # Update step counter
        self.step_label.config(text=f"Step {self.current_step + 1} of {len(self.config['actions'])}")
        
        # Load existing step name if present
        existing_name = action.get('step_name', '')
        self.step_name_var.set(existing_name)
        
        # IMMEDIATELY preview/highlight the element BEFORE user approval
        self.preview_element(action)
        
        # Describe action
        if action_type == 'click':
            self.action_label.config(text=f"Action: Click element\nSelector: {action.get('selector', 'N/A')[:80]}")
            self.data_label.config(text="")
            self.value_label.config(text="")
            self.reasoning_text.config(state='normal')
            self.reasoning_text.delete('1.0', 'end')
            self.reasoning_text.config(state='disabled')
            self.override_var.set("")
        elif action_type == 'navigate':
            self.action_label.config(text=f"Action: Navigate to\nURL: {action.get('url', 'N/A')}")
            self.data_label.config(text="")
            self.value_label.config(text="")
            self.reasoning_text.config(state='normal')
            self.reasoning_text.delete('1.0', 'end')
            self.reasoning_text.config(state='disabled')
            self.override_var.set("")
        elif action_type in ('input', 'select'):
            selector = action.get('selector', 'N/A')
            field_context = action.get('field_context', {})
            field_id = field_context.get('id', 'unknown')
            
            self.action_label.config(text=f"Action: {action_type.upper()}\nField ID: {field_id}\nSelector: {selector[:80]}")
            
            # Determine value and source
            csv_col = self.config['csv_mapping'].get(selector)
            value, source, reasoning = self.get_value_and_source(action, csv_col)
            
            self.data_label.config(text=f"Data Source: {source}")
            self.value_label.config(text=f"Value: {value}")
            
            # For SELECT actions, show dropdown with actual options
            if action_type == 'select':
                self.show_select_dropdown(action, value)
            else:
                # For INPUT, show text entry
                self.show_text_entry(value)
            
            # Show reasoning if LLM was used
            self.reasoning_text.config(state='normal')
            self.reasoning_text.delete('1.0', 'end')
            if reasoning:
                self.reasoning_text.insert('1.0', reasoning)
            self.reasoning_text.config(state='disabled')
        
        self.status_label.config(text=f"Element highlighted in browser. Review and approve or correct.")
    
    def show_select_dropdown(self, action, current_value):
        """Replace override entry with dropdown showing actual select options."""
        try:
            # Hide existing entry
            if hasattr(self, 'override_combo'):
                self.override_combo.pack_forget()
            self.override_entry.pack_forget()
            
            # Fetch options from page
            by_str = action.get('by', 'CSS_SELECTOR').upper()
            by = getattr(By, by_str)
            selector = action.get('selector')
            
            select_el = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((by, selector))
            )
            
            # Get all option texts
            from selenium.webdriver.support.ui import Select
            select_obj = Select(select_el)
            options = [opt.text.strip() for opt in select_obj.options if opt.text.strip()]
            
            # Create combobox
            self.override_combo = ttk.Combobox(
                self.override_entry.master,
                values=options,
                state='readonly',
                width=38
            )
            self.override_combo.pack(side='left', fill='x', expand=True, padx=5)
            
            # Set current value if it exists in options
            if current_value in options:
                self.override_combo.set(current_value)
            elif options:
                self.override_combo.set(options[0])
            
            # Sync with override_var
            def on_select_change(event):
                self.override_var.set(self.override_combo.get())
            self.override_combo.bind('<<ComboboxSelected>>', on_select_change)
            self.override_var.set(self.override_combo.get() if self.override_combo.get() else current_value)
            
            self.log(f"Loaded {len(options)} options from select dropdown")
            
        except Exception as e:
            # Fallback to text entry
            self.log(f"Could not fetch select options: {str(e)[:50]}")
            self.show_text_entry(current_value)
    
    def show_text_entry(self, current_value):
        """Show text entry for input fields."""
        # Hide combobox if it exists
        if hasattr(self, 'override_combo'):
            self.override_combo.pack_forget()
        
        # Show entry
        if not self.override_entry.winfo_ismapped():
            self.override_entry.pack(side='left', fill='x', expand=True, padx=5)
        
        self.override_var.set(current_value)
    
    def get_value_and_source(self, action, csv_col):
        """Determine what value will be used and why."""
        value = ""
        source = "Unknown"
        reasoning = ""
        
        if csv_col == '__RECORDED__':
            value = str(action.get('value', ''))
            source = "Recorded Value (from your demo)"
        elif csv_col and csv_col in self.test_row:
            value = str(self.test_row[csv_col])
            source = f"CSV Column: {csv_col}"
        else:
            # Would use LLM - simulate it
            field_context = action.get('field_context', {})
            config = load_llm_config()
            
            if config.get('enabled'):
                # Try to get LLM suggestion
                llm_value = infer_field_value_with_llm(field_context, self.test_row)
                if llm_value:
                    value = llm_value
                    source = "LLM Inference (AI-powered)"
                    reasoning = f"LLM analyzed CSV data:\n"
                    for k, v in self.test_row.items():
                        reasoning += f"  - {k}: {v}\n"
                    reasoning += f"\nField ID: {field_context.get('id', 'unknown')}\n"
                    reasoning += f"Suggested: {value}"
                    
                    if config.get('enable_search'):
                        reasoning += "\n\n(Web research was enabled for this inference)"
                else:
                    value = str(action.get('value', ''))
                    source = "Fallback: Recorded Value"
            else:
                value = str(action.get('value', ''))
                source = "Recorded Value (LLM disabled)"
        
        return value, source, reasoning
    
    def preview_element(self, action):
        """Preview/highlight element WITHOUT executing it."""
        try:
            action_type = action.get('action')
            by_str = action.get('by', 'CSS_SELECTOR').upper()
            selector = action.get('selector')
            
            # Validate selector before trying to use it
            if not selector or selector.strip() == '':
                self.log(f"Warning: Empty selector for {action_type} action")
                return
            
            if action_type in ('click', 'input', 'select'):
                try:
                    by = getattr(By, by_str)
                except AttributeError:
                    self.log(f"Warning: Invalid selector type '{by_str}'")
                    return
                
                try:
                    el = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    # Scroll into view and highlight
                    color = 'red' if action_type == 'click' else 'blue'
                    self.driver.execute_script(
                        f"arguments[0].scrollIntoView({{behavior: 'smooth', block: 'center'}});"
                        f"arguments[0].style.border='5px solid {color}';"
                        f"arguments[0].style.backgroundColor='rgba({255 if color=='red' else 0},{0},{255 if color=='blue' else 0},0.2)';",
                        el
                    )
                except Exception as find_error:
                    error_msg = f"Cannot preview element: {str(find_error)[:100]}"
                    self.log(error_msg)
                    if self.main_log:
                        self.main_log(f"[VERIFY WARN] {error_msg}\nSelector: {selector[:100]}\nBy: {by_str}")
        except Exception as e:
            self.log(f"Preview error: {str(e)[:50]}")
            if self.main_log:
                import traceback
                self.main_log(f"[VERIFY WARN] Preview failed: {e}\n{traceback.format_exc()}")
    
    def approve_step(self):
        """User approved current step, execute it and move to next."""
        try:
            action = self.config['actions'][self.current_step].copy()
            action_type = action.get('action', 'unknown')
            
            # Save step name if provided
            step_name = self.step_name_var.get().strip()
            if step_name:
                action['step_name'] = step_name
                self.config['actions'][self.current_step]['step_name'] = step_name
                self.log(f"Step {self.current_step + 1}: Named '{step_name}'")
            
            # Use override value if provided and different from original
            override = self.override_var.get().strip()
            if override and action.get('action') in ('input', 'select'):
                action['value'] = override
                # ALSO update the original config so corrections persist
                self.config['actions'][self.current_step]['value'] = override
                self.log(f"Step {self.current_step + 1}: Approved with override value: {override[:30]}")
            else:
                self.log(f"Step {self.current_step + 1}: Approved {action_type}")
            
            # Execute the action
            self.execute_action(action)
            
            # Save verified action with corrected value and name
            self.verified_actions.append(action)
            
            # Auto-save after every approval
            self.save_verification_progress()
            
            # Move to next step
            self.current_step += 1
            self.show_step()
            
        except Exception as e:
            error_msg = f"Failed to execute step: {e}"
            import traceback
            full_trace = traceback.format_exc()
            self.log(f"ERROR: {error_msg}")
            if self.main_log:
                self.main_log(f"[VERIFY ERROR] Step {self.current_step + 1}: {error_msg}\n{full_trace}")
            
            # Offer recovery options
            response = messagebox.askyesnocancel(
                "Execution Error",
                f"{error_msg}\n\n"
                f"Yes = Skip this step and continue\n"
                f"No = Go back to previous step\n"
                f"Cancel = Stay on this step to retry"
            )
            
            if response is True:  # Yes - Skip
                self.log("Skipping failed step and continuing...")
                self.current_step += 1
                self.show_step()
            elif response is False:  # No - Go back
                if self.current_step > 0:
                    self.log("Going back to previous step...")
                    if self.verified_actions:
                        self.verified_actions.pop()
                    self.current_step -= 1
                    self.save_verification_progress()
                    self.show_step()
                else:
                    self.log("Cannot go back - already at first step")
            # else: Cancel - stay on current step
    
    def skip_step(self):
        """Skip current step without executing."""
        # Save step name even when skipping
        step_name = self.step_name_var.get().strip()
        if step_name:
            self.config['actions'][self.current_step]['step_name'] = step_name
            self.log(f"Step {self.current_step + 1}: Named '{step_name}' (skipped)")
        else:
            self.log(f"Step {self.current_step + 1}: Skipped (not executed)")
        
        # Auto-save after skip
        self.save_verification_progress()
        
        self.current_step += 1
        self.show_step()
    
    def go_previous(self):
        """Go back to the previous step."""
        if self.current_step > 0:
            # Save current step name before going back
            step_name = self.step_name_var.get().strip()
            if step_name:
                self.config['actions'][self.current_step]['step_name'] = step_name
            
            # Remove last verified action if going backwards
            if self.verified_actions:
                self.verified_actions.pop()
            
            # Auto-save before moving back
            self.save_verification_progress()
            
            self.current_step -= 1
            self.show_step()
            self.log("Moved to previous step. Re-verify or make changes.")
        else:
            self.log("Already at first step.")
    
    def delete_step(self):
        """Delete current step from workflow (soft delete - can be restored)."""
        action = self.config['actions'][self.current_step].copy()
        action_desc = f"{action.get('action', 'unknown')} - {action.get('selector', 'N/A')[:50]}"
        
        # Save step name before deleting
        step_name = self.step_name_var.get().strip()
        if step_name:
            action['step_name'] = step_name
        
        response = messagebox.askyesno(
            "Delete Step",
            "Delete this step?\n\n"
            "Step will be moved to deleted list and can be restored.\n"
            "Permanent deletion only available after full verification."
        )
        if response:
            # Add to deleted steps list with position info
            action['original_position'] = self.current_step
            self.deleted_steps.append(action)
            
            # Remove from actions list
            del self.config['actions'][self.current_step]
            self.log(f"Step {self.current_step + 1} DELETED: {action_desc} (can restore)")
            
            # Auto-save after deletion
            self.save_verification_progress()
            
            # Show next step (or complete if that was the last one)
            self.show_step()
    
    def restore_deleted(self):
        """Show list of deleted steps and allow restoration."""
        if not self.deleted_steps:
            messagebox.showinfo("No Deleted Steps", "No steps have been deleted yet.")
            return
        
        # Create restore dialog
        restore_win = tk.Toplevel(self.window)
        restore_win.title("Restore Deleted Steps")
        restore_win.geometry("600x400")
        
        ttk.Label(restore_win, text="Deleted Steps (click to restore)", font=('Arial', 12, 'bold')).pack(pady=10)
        
        # Scrollable list
        list_frame = ttk.Frame(restore_win)
        list_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side='right', fill='y')
        
        listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, font=('Courier', 9))
        listbox.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=listbox.yview)
        
        # Populate with deleted steps
        for i, step in enumerate(self.deleted_steps):
            step_name = step.get('step_name', '')
            action_type = step.get('action', 'unknown')
            selector = step.get('selector', 'N/A')[:40]
            orig_pos = step.get('original_position', '?')
            
            display = f"#{i+1} [Pos:{orig_pos+1}] {action_type.upper()}"
            if step_name:
                display += f" - {step_name}"
            display += f" | {selector}"
            listbox.insert('end', display)
        
        def on_restore():
            selection = listbox.curselection()
            if not selection:
                messagebox.showwarning("No Selection", "Please select a step to restore.")
                return
            
            idx = selection[0]
            restored_step = self.deleted_steps.pop(idx)
            
            # Insert back at current position
            self.config['actions'].insert(self.current_step, restored_step)
            
            step_desc = f"{restored_step.get('action', 'unknown')} - {restored_step.get('step_name', 'unnamed')}"
            self.log(f"Restored step: {step_desc} at position {self.current_step + 1}")
            
            # Auto-save after restore
            self.save_verification_progress()
            
            # Refresh current view
            self.show_step()
            restore_win.destroy()
        
        btn_frame = ttk.Frame(restore_win)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Restore Selected", command=on_restore).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Close", command=restore_win.destroy).pack(side='left', padx=5)
        
        restore_win.transient(self.window)
        restore_win.grab_set()
    
    def log(self, msg):
        """Log message to status label and main output."""
        self.status_label.config(text=msg)
        if self.main_log:
            self.main_log(f"[VERIFY] {msg}")
    
    def override_element(self):
        """Allow user to click on page to select a new element for this step."""
        try:
            action = self.config['actions'][self.current_step]
            action_type = action.get('action')
            
            if action_type not in ('click', 'input', 'select'):
                messagebox.showinfo("Not Applicable", "Element override only works for click, input, and select actions.")
                return
            
            # Store current URL to detect navigation
            self.override_start_url = self.driver.current_url
            
            self.element_status.config(text="Click on an element in the browser...")
            self.log("Waiting for you to click an element on the page...")
            
            # Inject click capture script
            capture_script = r"""
            (function() {
                window._elementOverride = null;
                
                function cssPath(el) {
                    if (!(el instanceof Element)) return '';
                    
                    // Try to build a simple, reliable selector
                    // Priority: ID > class > tag with nth-of-type
                    
                    // If element has ID, use it directly
                    if (el.id && /^[a-zA-Z][a-zA-Z0-9_-]*$/.test(el.id)) {
                        return '#' + el.id;
                    }
                    
                    // Try to build a short path
                    var path = [];
                    var current = el;
                    
                    while (current && current.nodeType === Node.ELEMENT_NODE && path.length < 5) {
                        var selector = current.nodeName.toLowerCase();
                        
                        // Check for ID on parent
                        if (current.id && /^[a-zA-Z][a-zA-Z0-9_-]*$/.test(current.id)) {
                            selector += '#' + current.id;
                            path.unshift(selector);
                            break;
                        }
                        
                        // Add class if present (first class only, if valid)
                        if (current.className && typeof current.className === 'string') {
                            var classes = current.className.trim().split(/\s+/);
                            if (classes.length > 0 && /^[a-zA-Z_-][a-zA-Z0-9_-]*$/.test(classes[0])) {
                                selector += '.' + classes[0];
                            }
                        }
                        
                        // Add nth-of-type for specificity
                        var sib = current;
                        var nth = 1;
                        while (sib = sib.previousElementSibling) {
                            if (sib.nodeName.toLowerCase() === current.nodeName.toLowerCase()) nth++;
                        }
                        if (nth > 1 || !current.className) {
                            selector += ':nth-of-type(' + nth + ')';
                        }
                        
                        path.unshift(selector);
                        current = current.parentNode;
                    }
                    
                    return path.join(' > ');
                }
                
                function captureClick(e) {
                    // COMPLETELY stop the click from doing anything
                    e.preventDefault();
                    e.stopPropagation();
                    e.stopImmediatePropagation();
                    
                    var target = e.target;
                    
                    // If it's a link or button, explicitly prevent default action
                    if (target.tagName === 'A' || target.tagName === 'BUTTON') {
                        e.preventDefault();
                    }
                    
                    window._elementOverride = {
                        tag: target.tagName.toLowerCase(),
                        id: target.id || null,
                        name: target.name || null,
                        type: target.type || null,
                        cssPath: cssPath(target),
                        text: target.textContent.substring(0, 50)
                    };
                    
                    // Visual feedback
                    target.style.border = '5px solid green';
                    target.style.backgroundColor = 'rgba(0,255,0,0.3)';
                    target.style.outline = '3px dashed lime';
                    
                    // Remove listener
                    document.removeEventListener('click', captureClick, true);
                    
                    // Return false to be extra sure
                    return false;
                }
                
                document.addEventListener('click', captureClick, true);
                return true;
            })();
            """
            
            self.driver.execute_script(capture_script)
            
            # Poll for click
            self.window.after(100, self._check_element_override)
            
        except Exception as e:
            messagebox.showerror("Override Error", f"Failed to set up element override: {e}")
            self.element_status.config(text="")
    
    def _check_element_override(self):
        """Poll for user click on page."""
        try:
            # Check if page navigated during override
            current_url = self.driver.current_url
            if hasattr(self, 'override_start_url') and current_url != self.override_start_url:
                # Page changed - need to re-inject script
                time.sleep(0.5)  # Wait for page to stabilize
                self.override_start_url = current_url
                # Re-inject the capture script
                capture_script = r"""
                (function() {
                    window._elementOverride = null;
                    
                    function cssPath(el) {
                        if (!(el instanceof Element)) return '';
                        
                        // If element has ID, use it directly
                        if (el.id && /^[a-zA-Z][a-zA-Z0-9_-]*$/.test(el.id)) {
                            return '#' + el.id;
                        }
                        
                        // Build a short path
                        var path = [];
                        var current = el;
                        
                        while (current && current.nodeType === Node.ELEMENT_NODE && path.length < 5) {
                            var selector = current.nodeName.toLowerCase();
                            
                            if (current.id && /^[a-zA-Z][a-zA-Z0-9_-]*$/.test(current.id)) {
                                selector += '#' + current.id;
                                path.unshift(selector);
                                break;
                            }
                            
                            if (current.className && typeof current.className === 'string') {
                                var classes = current.className.trim().split(/\s+/);
                                if (classes.length > 0 && /^[a-zA-Z_-][a-zA-Z0-9_-]*$/.test(classes[0])) {
                                    selector += '.' + classes[0];
                                }
                            }
                            
                            var sib = current;
                            var nth = 1;
                            while (sib = sib.previousElementSibling) {
                                if (sib.nodeName.toLowerCase() === current.nodeName.toLowerCase()) nth++;
                            }
                            if (nth > 1 || !current.className) {
                                selector += ':nth-of-type(' + nth + ')';
                            }
                            
                            path.unshift(selector);
                            current = current.parentNode;
                        }
                        
                        return path.join(' > ');
                    }
                    
                    function captureClick(e) {
                        // COMPLETELY stop the click from doing anything
                        e.preventDefault();
                        e.stopPropagation();
                        e.stopImmediatePropagation();
                        
                        var target = e.target;
                        
                        // If it's a link or button, explicitly prevent default action
                        if (target.tagName === 'A' || target.tagName === 'BUTTON') {
                            e.preventDefault();
                        }
                        
                        window._elementOverride = {
                            tag: target.tagName.toLowerCase(),
                            id: target.id || null,
                            name: target.name || null,
                            type: target.type || null,
                            cssPath: cssPath(target),
                            text: target.textContent.substring(0, 50)
                        };
                        
                        target.style.border = '5px solid green';
                        target.style.backgroundColor = 'rgba(0,255,0,0.3)';
                        target.style.outline = '3px dashed lime';
                        
                        document.removeEventListener('click', captureClick, true);
                        
                        return false;
                    }
                    
                    document.addEventListener('click', captureClick, true);
                    return true;
                })();
                """
                try:
                    self.driver.execute_script(capture_script)
                    self.log("Page navigated - override mode re-activated on new page")
                except Exception as e:
                    print(f"Failed to re-inject: {e}")
            
            result = self.driver.execute_script("return window._elementOverride;")
            
            if result:
                # User clicked an element!
                css_path = result.get('cssPath', '')
                element_tag = result.get('tag', 'unknown')
                element_id = result.get('id', '')
                element_text = result.get('text', '')[:30]
                
                # Update current action's selector
                action = self.config['actions'][self.current_step]
                action['by'] = 'CSS_SELECTOR'
                action['selector'] = css_path
                
                # Update field context
                if 'field_context' not in action:
                    action['field_context'] = {}
                action['field_context']['id'] = element_id
                action['field_context']['tag'] = element_tag
                
                self.element_status.config(text=f" Element updated: {element_tag}#{element_id if element_id else 'no-id'}")
                self.log(f"Element overridden! New selector: {css_path[:80]}...")
                
                # Re-highlight the new element
                self.preview_element(action)
                
                # Clear the capture
                self.driver.execute_script("window._elementOverride = null;")
            else:
                # Keep polling
                self.window.after(100, self._check_element_override)
        except Exception as e:
            self.element_status.config(text="Polling stopped")
            print(f"Element override polling error: {e}")
    
    def execute_action(self, action):
        """Execute a single action in the browser."""
        action_type = action.get('action')
        by = getattr(By, action.get('by', 'CSS_SELECTOR').upper())
        selector = action.get('selector')
        
        if action_type == 'navigate':
            self.driver.get(action.get('url'))
            time.sleep(1)
        elif action_type == 'click':
            # Find element
            el = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((by, selector))
            )
            # Highlight briefly before click
            try:
                self.driver.execute_script(
                    "arguments[0].style.border='3px solid orange';"
                    "arguments[0].style.backgroundColor='rgba(255,165,0,0.3)';",
                    el
                )
            except Exception:
                pass  # Ignore highlight errors
            
            time.sleep(0.5)
            
            # Re-find element if it became stale
            try:
                el.click()
            except Exception as e:
                if 'stale' in str(e).lower():
                    self.log("Element became stale, re-finding...")
                    el = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    el.click()
                else:
                    raise
            
            time.sleep(0.5)
            
            # Try to remove highlighting (may fail if page navigated)
            try:
                self.driver.execute_script(
                    "arguments[0].style.border='';"
                    "arguments[0].style.backgroundColor='';",
                    el
                )
            except Exception:
                pass  # Ignore cleanup errors if element is gone
        elif action_type == 'input':
            el = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((by, selector))
            )
            # Scroll into view and highlight element
            self.driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});"
                "arguments[0].style.border='5px solid blue';"
                "arguments[0].style.backgroundColor='rgba(0,0,255,0.2)';",
                el
            )
            time.sleep(0.8)
            el.clear()
            el.send_keys(action.get('value', ''))
            time.sleep(0.3)
            # Remove highlighting
            self.driver.execute_script(
                "arguments[0].style.border='';"
                "arguments[0].style.backgroundColor='';",
                el
            )
        elif action_type == 'select':
            from selenium.webdriver.support.ui import Select
            el = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((by, selector))
            )
            # Scroll into view and highlight element
            self.driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});"
                "arguments[0].style.border='5px solid blue';"
                "arguments[0].style.backgroundColor='rgba(0,0,255,0.2)';",
                el
            )
            time.sleep(0.8)
            select = Select(el)
            select.select_by_visible_text(action.get('value', ''))
            time.sleep(0.3)
            # Remove highlighting
            self.driver.execute_script(
                "arguments[0].style.border='';"
                "arguments[0].style.backgroundColor='';",
                el
            )
        
        time.sleep(0.3)
    
    def save_verification_progress(self):
        """Save verified actions and deleted steps to prevent data loss."""
        try:
            verified_config = self.config.copy()
            verified_config['actions'] = self.verified_actions
            verified_config['deleted_steps'] = self.deleted_steps  # Save deleted steps too
            verified_config['verification_complete'] = False
            
            site_name = self.config.get('site_name', 'workflow')
            filename = f'configs/{site_name}_verified_partial.json'
            with open(filename, 'w') as f:
                json.dump(verified_config, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to save verification progress: {e}")
    
    def complete_verification(self):
        """All steps verified successfully."""
        self.approved = True
        
        # Offer permanent deletion of deleted steps
        if self.deleted_steps:
            response = messagebox.askyesno(
                "Deleted Steps",
                f"{len(self.deleted_steps)} step(s) were deleted during verification.\n\n"
                f"Do you want to PERMANENTLY delete them?\n\n"
                f"Choose No to keep them in case you want to restore them later."
            )
            if not response:
                # Keep deleted steps in config for later review
                pass
            else:
                # Permanently discard deleted steps
                self.log(f"Permanently deleted {len(self.deleted_steps)} steps")
                self.deleted_steps.clear()
        
        # Save final verified workflow with all corrections
        try:
            # Use the corrected config (which has been updated during approve_step)
            verified_config = self.config.copy()
            # Override actions with verified actions (which include corrections)
            verified_config['actions'] = self.verified_actions
            verified_config['verification_complete'] = True
            
            # Save deleted steps if user chose to keep them
            if self.deleted_steps:
                verified_config['deleted_steps_archive'] = self.deleted_steps
                self.log(f"Saved {len(self.deleted_steps)} deleted steps to archive")
            
            site_name = self.config.get('site_name', 'workflow')
            filename = f'configs/{site_name}_workflow.json'
            with open(filename, 'w') as f:
                json.dump(verified_config, f, indent=2)
            
            # Also clean up partial file
            partial_file = f'configs/{site_name}_verified_partial.json'
            if os.path.exists(partial_file):
                os.remove(partial_file)
            
            messagebox.showinfo(
                "Verification Complete",
                f"Workflow verified and saved successfully!\n\n"
                f"Total steps: {len(self.verified_actions)}\n"
                f"Deleted steps: {len(self.deleted_steps) if self.deleted_steps else 0} archived\n"
                f"All corrections have been saved.\n\n"
                f"You can now run this workflow on all CSV rows."
            )
        except Exception as e:
            messagebox.showerror("Save Error", f"Verification complete but failed to save: {e}")
        
        self.on_close()
    
    def on_close(self):
        """Clean up and close."""
        self.log(f"Verification cancelled at step {self.current_step + 1}. {len(self.verified_actions)} steps were verified.")
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
        self.window.destroy()

# -------------------------
# Save Config
# -------------------------

def save_config(site_name, url, creds, actions, csv_mapping):
    os.makedirs('configs', exist_ok=True)
    config = {
        'site_name': site_name,
        'url': url,
        'credentials': creds,
        'actions': actions,
        'csv_mapping': csv_mapping
    }
    filename = f'configs/{site_name}_workflow.json'
    with open(filename, 'w') as f:
        json.dump(config, f, indent=2)
    return filename

# -------------------------
# Preferences (persist UI values)
# -------------------------

def load_prefs():
    try:
        if os.path.exists(PREFS_FILE):
            with open(PREFS_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_prefs(data: dict):
    try:
        os.makedirs('configs', exist_ok=True)
        with open(PREFS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# -------------------------
# Replay Automation
# -------------------------

def replay_workflow(config_file, csv_file, headless=False):
    with open(config_file) as f:
        config = json.load(f)
    df = pd.read_csv(csv_file)
    driver = init_driver(headless=headless)
    driver.get(config['url'])

    # Optional login
    creds = config.get('credentials')
    if creds:
        try:
            username_field = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            username_field.send_keys(creds.get('username',''))
            password_field = driver.find_element(By.NAME, "password")
            password_field.send_keys(creds.get('password',''))
            driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        except Exception as e:
            print(f"Login skipped/failed: {e}")

    for idx, row in df.iterrows():
        print(f"\nProcessing row {idx + 1}/{len(df)}...")
        csv_row_dict = row.to_dict()
        
        # Determine which steps to execute
        loop_start = config.get('loop_start_step', 0)
        actions_to_execute = config['actions']
        
        if idx > 0 and loop_start > 0:
            # On 2nd+ iterations, start from loop point
            actions_to_execute = config['actions'][loop_start:]
            print(f"  Starting from step {loop_start + 1} (skipping login/setup steps)")
        
        for action in actions_to_execute:
            try:
                action_type = action.get('action')
                by = getattr(By, action.get('by', 'CSS_SELECTOR').upper())
                selector = action.get('selector')
                if not selector:
                    continue
                
                if action_type == 'navigate':
                    # Handle navigation actions
                    nav_url = action.get('url')
                    if nav_url:
                        driver.get(nav_url)
                        time.sleep(1)
                    continue
                    
                if action_type == 'click':
                    el = driver.find_element(by, selector)
                    el.click()
                    
                elif action_type == 'input':
                    el = driver.find_element(by, selector)
                    csv_col = config['csv_mapping'].get(selector)
                    value = None
                    
                    if csv_col == '__RECORDED__':
                        # User explicitly chose to use recorded value
                        value = str(action.get('value', ''))
                        print(f"  Input field '{selector[:50]}...': Using recorded value '{value}'")
                    elif csv_col and csv_col in row:
                        # Direct CSV mapping
                        value = str(row[csv_col])
                        print(f"  Input field '{selector[:50]}...': Using CSV column '{csv_col}' = '{value}'")
                    else:
                        # Try LLM inference for unmapped field
                        field_context = action.get('field_context', {})
                        llm_value = infer_field_value_with_llm(field_context, csv_row_dict)
                        if llm_value:
                            value = llm_value
                            print(f"  Input field '{selector[:50]}...': LLM suggested '{value}'")
                        else:
                            # Fallback to recorded value
                            value = str(action.get('value', ''))
                            print(f"  Input field '{selector[:50]}...': Using recorded value '{value}'")
                    
                    if value is not None:
                        el.clear()
                        el.send_keys(value)
                        
                elif action_type == 'select':
                    from selenium.webdriver.support.ui import Select
                    el = Select(driver.find_element(by, selector))
                    csv_col = config['csv_mapping'].get(selector)
                    value = None
                    
                    # Get available options
                    available_options = [opt.text for opt in el.options if opt.text.strip()]
                    
                    if csv_col == '__RECORDED__':
                        # User explicitly chose to use recorded value
                        value = str(action.get('value', ''))
                        print(f"  Select field '{selector[:50]}...': Using recorded value '{value}'")
                    elif csv_col and csv_col in row:
                        # Direct CSV mapping
                        value = str(row[csv_col])
                        print(f"  Select field '{selector[:50]}...': Using CSV column '{csv_col}' = '{value}'")
                    else:
                        # Try LLM inference for unmapped select
                        field_context = action.get('field_context', {})
                        llm_value = infer_field_value_with_llm(field_context, csv_row_dict, available_options=available_options)
                        if llm_value:
                            value = llm_value
                            print(f"  Select field '{selector[:50]}...': LLM suggested '{value}' from options: {available_options}")
                        else:
                            # Fallback to recorded value
                            value = str(action.get('value', ''))
                            print(f"  Select field '{selector[:50]}...': Using recorded value '{value}'")
                    
                    if value is not None and value != '':
                        el.select_by_visible_text(value)
                        
                time.sleep(0.5)
            except Exception as e:
                print(f"Error on action {action}: {e}")
    
    print("\nWorkflow completed for all rows.")
    driver.quit()

def replay_workflow_http(config_file, csv_file):
    """
    Replay a workflow without a browser by submitting the first <form> on the page.
    Uses the saved csv_mapping where keys are detected field identifiers and values are CSV column names.
    The payload keys prefer each field's 'name' attribute; falling back to 'id' if missing.
    """
    with open(config_file) as f:
        config = json.load(f)
    url = config['url']
    df = pd.read_csv(csv_file)

    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36'
    }
    # Load the page to discover the form
    resp = session.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')
    form = soup.find('form')
    if not form:
        raise RuntimeError('No <form> found on the page for HTTP submission mode.')
    method = (form.get('method') or 'get').lower()
    action = form.get('action') or url
    if not action.startswith('http'):
        # Resolve relative action
        from urllib.parse import urljoin
        action = urljoin(url, action)

    # Build a lookup of detected fields: prefer name then id
    detected = detect_fields_via_requests(url)
    # Map selector keys to payload key (field name or id)
    selector_to_payload_key = {}
    for key, info in detected.items():
        payload_key = info.get('name') or info.get('id')
        if payload_key:
            selector_to_payload_key[key] = payload_key

    for _, row in df.iterrows():
        data = {}
        for selector_key, csv_col in config['csv_mapping'].items():
            if not csv_col:
                continue
            payload_key = selector_to_payload_key.get(selector_key)
            if payload_key and csv_col in row:
                data[payload_key] = str(row[csv_col])
        if method == 'post':
            r = session.post(action, data=data, headers=headers, timeout=30)
        else:
            r = session.get(action, params=data, headers=headers, timeout=30)
        # Basic status check
        if r.status_code >= 400:
            raise RuntimeError(f'Form submission failed with status {r.status_code} at {action}')

#############################
# Tkinter GUI Implementation #
#############################

class LeadAutomationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Lead Automation Tool")

        self.fields = {}
        self.csv_mapping = {}
        self.actions_log = []

        # Inputs frame
        frm_inputs = ttk.Frame(root, padding=10)
        frm_inputs.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frm_inputs, text="Site Name:").grid(row=0, column=0, sticky="w")
        self.ent_site_name = ttk.Entry(frm_inputs, width=40)
        self.ent_site_name.grid(row=0, column=1, sticky="ew")

        ttk.Label(frm_inputs, text="Site URL:").grid(row=1, column=0, sticky="w")
        self.ent_site_url = ttk.Entry(frm_inputs, width=40)
        self.ent_site_url.grid(row=1, column=1, sticky="ew")

        ttk.Label(frm_inputs, text="Username:").grid(row=2, column=0, sticky="w")
        self.ent_username = ttk.Entry(frm_inputs, width=40)
        self.ent_username.grid(row=2, column=1, sticky="ew")

        ttk.Label(frm_inputs, text="Password:").grid(row=3, column=0, sticky="w")
        self.ent_password = ttk.Entry(frm_inputs, width=40, show="*")
        self.ent_password.grid(row=3, column=1, sticky="ew")

        ttk.Label(frm_inputs, text="CSV File:").grid(row=4, column=0, sticky="w")
        self.ent_csv = ttk.Entry(frm_inputs, width=40)
        self.ent_csv.grid(row=4, column=1, sticky="ew")
        ttk.Button(frm_inputs, text="Browse", command=self.browse_csv).grid(row=4, column=2, padx=(5,0))

        # Buttons frame
        frm_buttons = ttk.Frame(root, padding=(10,0,10,10))
        frm_buttons.grid(row=1, column=0, sticky="ew")

        ttk.Button(frm_buttons, text="Detect Fields", command=self.on_detect_fields).grid(row=0, column=0, padx=5, pady=5)
        ttk.Button(frm_buttons, text="Map CSV", command=self.on_map_csv).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(frm_buttons, text="Save Config", command=self.on_save_config).grid(row=0, column=2, padx=5, pady=5)
        ttk.Button(frm_buttons, text="LLM Settings", command=self.on_llm_settings).grid(row=0, column=3, padx=5, pady=5)
        ttk.Button(frm_buttons, text="Edit Workflow", command=self.on_edit_workflow).grid(row=0, column=4, padx=5, pady=5)
        ttk.Button(frm_buttons, text="Verify Workflow", command=self.on_verify_workflow).grid(row=0, column=5, padx=5, pady=5)
        ttk.Button(frm_buttons, text="Run Workflow", command=self.on_run_workflow_browser).grid(row=0, column=6, padx=5, pady=5)

        # Output box
        frm_output = ttk.Frame(root, padding=(10,0,10,10))
        frm_output.grid(row=2, column=0, sticky="nsew")
        self.txt_output = tk.Text(frm_output, width=100, height=20)
        self.txt_output.grid(row=0, column=0, sticky="nsew")

        # Utility buttons row (copy/save log)
        frm_utils = ttk.Frame(root, padding=(10,0,10,10))
        frm_utils.grid(row=3, column=0, sticky="ew")
        ttk.Button(frm_utils, text="Copy Output", command=self.copy_output).grid(row=0, column=0, padx=5, pady=5)
        ttk.Button(frm_utils, text="Save Log", command=self.save_log).grid(row=0, column=1, padx=5, pady=5)

        # Configure resizing
        root.grid_rowconfigure(2, weight=1)
        root.grid_columnconfigure(0, weight=1)
        frm_output.grid_rowconfigure(0, weight=1)
        frm_output.grid_columnconfigure(0, weight=1)

        # Load last session preferences
        self.apply_prefs(load_prefs())

    def log(self, msg: str):
        self.txt_output.insert("end", msg + "\n")
        self.txt_output.see("end")

    def collect_prefs(self) -> dict:
        return {
            'site_name': self.ent_site_name.get().strip(),
            'site_url': self.ent_site_url.get().strip(),
            'username': self.ent_username.get().strip(),
            'password': self.ent_password.get().strip(),
            'csv_file': self.ent_csv.get().strip(),
            'fields': self.fields,
            'csv_mapping': self.csv_mapping,
            'actions_log': self.actions_log,
        }

    def apply_prefs(self, prefs: dict):
        try:
            if 'site_name' in prefs: self.ent_site_name.delete(0, 'end'); self.ent_site_name.insert(0, prefs['site_name'])
            if 'site_url' in prefs: self.ent_site_url.delete(0, 'end'); self.ent_site_url.insert(0, prefs['site_url'])
            if 'username' in prefs: self.ent_username.delete(0, 'end'); self.ent_username.insert(0, prefs['username'])
            if 'password' in prefs: self.ent_password.delete(0, 'end'); self.ent_password.insert(0, prefs['password'])
            if 'csv_file' in prefs: self.ent_csv.delete(0, 'end'); self.ent_csv.insert(0, prefs['csv_file'])
            if 'fields' in prefs and isinstance(prefs['fields'], dict): self.fields = prefs['fields']
            if 'csv_mapping' in prefs and isinstance(prefs['csv_mapping'], dict): self.csv_mapping = prefs['csv_mapping']
            if 'actions_log' in prefs and isinstance(prefs['actions_log'], list): 
                self.actions_log = prefs['actions_log']
                if self.actions_log:
                    self.log(f"Loaded {len(self.actions_log)} saved workflow steps from last session.")
        except Exception:
            pass

    def copy_output(self):
        try:
            text = self.txt_output.get('1.0', 'end').strip()
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()  # keep clipboard after window closes
            messagebox.showinfo("Copied", "Output copied to clipboard.")
        except Exception as e:
            messagebox.showerror("Copy Failed", str(e))

    def save_log(self):
        try:
            text = self.txt_output.get('1.0', 'end')
            path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=(("Text Files","*.txt"),("All Files","*.*")))
            if path:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(text)
                self.log(f"Log saved to {path}")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))

    def browse_csv(self):
        path = filedialog.askopenfilename(filetypes=(("CSV Files", "*.csv"), ("All Files", "*.*")))
        if path:
            self.ent_csv.delete(0, "end")
            self.ent_csv.insert(0, path)
            save_prefs(self.collect_prefs())

    def on_detect_fields(self):
        site_url = self.ent_site_url.get().strip()
        if not site_url:
            messagebox.showwarning("Missing URL", "Please enter the Site URL.")
            return
        self.log("Opening browser to detect fields and start recording...")
        self.actions_log = []
        try:
            # Try live browser detection first
            driver = init_driver(parent=self.root)
            driver.get(site_url)

            # Inject recorder and detect dynamic fields from the rendered page
            inject_recorder(driver)
            self.log("Waiting for page to load and fields to render...")
            self.fields = detect_dynamic_fields(driver)
            if self.fields:
                self.log(f"Detected {len(self.fields)} fields (live):\n" + json.dumps(self.fields, indent=2))
            else:
                self.log("No fields detected. Page may not have input/select/textarea elements, or they may be dynamically added after user interaction.")
            save_prefs(self.collect_prefs())

            # Start immediate recording session
            messagebox.showinfo(
                "Recording Started",
                "Browser launched. Perform your actions now (clicks, inputs, selects).\n"
                "Navigate across pages as needed - recording continues.\n"
                "Close the browser window when finished to stop recording."
            )
            
            # Track URL changes and re-inject recorder on new pages
            last_url = driver.current_url
            while True:
                try:
                    current_url = driver.current_url
                    
                    # Detect page navigation
                    if current_url != last_url:
                        self.log(f"Page navigated: {current_url}")
                        # Log navigation as a special action
                        self.actions_log.append({
                            'action': 'navigate',
                            'url': current_url,
                            'from_url': last_url
                        })
                        # Re-inject recorder on new page
                        time.sleep(0.5)  # Brief wait for page to stabilize
                        inject_recorder(driver)
                        last_url = current_url
                    
                    # Drain any recorded events from the page and append to actions_log
                    try:
                        events = driver.execute_script('return window._lgDrain ? window._lgDrain() : [];')
                    except Exception:
                        events = []
                    if events:
                        for ev in events:
                            act = build_action_from_event(ev)
                            if act:
                                self.actions_log.append(act)
                    time.sleep(0.75)
                except Exception:
                    break
            self.log("Browser closed. Recording finished.")
            
            # Deduplicate actions to keep only final values
            if self.actions_log:
                original_count = len(self.actions_log)
                self.actions_log = deduplicate_actions(self.actions_log)
                deduped_count = len(self.actions_log)
                if deduped_count < original_count:
                    self.log(f"Deduplicated {original_count} actions down to {deduped_count} (kept final values only).")
                
                # Display with step numbers for readability
                self.log("\n=== Recorded Workflow Steps ===")
                for i, act in enumerate(self.actions_log, 1):
                    action_type = act.get('action', 'unknown')
                    if action_type == 'navigate':
                        self.log(f"Step {i}: Navigate to {act.get('url', 'unknown')}")
                    elif action_type == 'click':
                        self.log(f"Step {i}: Click element (by {act.get('by', 'unknown')})")
                    elif action_type == 'input':
                        val_preview = act.get('value', '')[:30] + ('...' if len(act.get('value', '')) > 30 else '')
                        self.log(f"Step {i}: Input '{val_preview}' (by {act.get('by', 'unknown')})")
                    elif action_type == 'select':
                        self.log(f"Step {i}: Select '{act.get('value', '')}' (by {act.get('by', 'unknown')})")
                
                self.log("\n=== Full Action Details ===\n" + json.dumps(self.actions_log, indent=2))
            else:
                self.log("No actions recorded. (Recording hooks can be added to capture clicks/inputs automatically.)")
            
            # Save workflow to persist it
            save_prefs(self.collect_prefs())
            self.log("\nWorkflow saved! You can now Map CSV, Save Config, and Run Workflow without re-recording.")

        except Exception as e:
            # Fallback to HTTP detection if browser cannot be started
            self.log(f"Browser-based detection failed ({e}). Falling back to HTTP mode...")
            try:
                self.fields = detect_fields_via_requests(site_url)
                self.log("Detected Fields (HTTP):\n" + json.dumps(self.fields, indent=2))
            except Exception as ee:
                self.log(f"Error detecting fields (HTTP fallback): {ee}")
            finally:
                save_prefs(self.collect_prefs())

    def on_record_workflow(self):
        site_url = self.ent_site_url.get().strip()
        if not site_url:
            messagebox.showwarning("Missing URL", "Please enter the Site URL.")
            return

        self.log("Preparing to record workflow...")

        # Reset actions log
        self.actions_log = []

        try:
            # Initialize browser with user-friendly selection
            driver = init_driver(parent=self.root)
            driver.get(site_url)

            messagebox.showinfo(
                "Record Workflow",
                "Browser launched for workflow recording.\n\n"
                "Please perform your actions (clicks, inputs, selects) manually.\n"
                "Once done, close the browser window to finish recording."
            )

            # Polling loop: Wait for browser to close
            while True:
                try:
                    _ = driver.title  # Access property to check if still open
                    time.sleep(1)
                except Exception:
                    break

            self.log("Browser closed. Recording finished.")

            # Show recorded actions
            if self.actions_log:
                self.log("Recorded Actions:\n" + json.dumps(self.actions_log, indent=2))
            else:
                self.log("No actions recorded. Make sure to interact with the browser while it was open.")

        except RuntimeError as e:
            messagebox.showerror("Driver Error", str(e))
            self.log(f"Workflow recording aborted: {e}")
        except Exception as e:
            messagebox.showerror("Unexpected Error", str(e))
            self.log(f"Unexpected error during workflow recording: {e}")

    def on_map_csv(self):
        csv_file = self.ent_csv.get().strip()
        if not csv_file:
            self.log("Please select a CSV file.")
            return
        
        # Check if we have recorded actions with inputs/selects
        if not self.actions_log:
            self.log("No workflow recorded yet. Please run 'Detect Fields' first and perform your actions.")
            return
        
        input_actions = [a for a in self.actions_log if a.get('action') in ('input', 'select')]
        if not input_actions:
            self.log("No input or select fields in recorded workflow. Nothing to map.")
            return
        
        try:
            # Load existing mapping from config file if it exists
            existing_mapping = self.csv_mapping.copy() if self.csv_mapping else {}
            site_name = self.ent_site_name.get().strip()
            if site_name:
                config_file = f'configs/{site_name}_workflow.json'
                if os.path.exists(config_file):
                    try:
                        with open(config_file, 'r') as f:
                            config = json.load(f)
                        if 'csv_mapping' in config:
                            existing_mapping = config['csv_mapping']
                            self.log(f"Loaded existing mapping from {config_file}")
                    except Exception as e:
                        self.log(f"Could not load existing mapping: {e}")
            
            self.log(f"Opening CSV mapping window for {len(input_actions)} fields...")
            self.csv_mapping, unmapped = map_csv_to_actions(csv_file, self.actions_log, existing_mapping, parent=self.root)
            
            if self.csv_mapping:
                output_text = "CSV Mapping Results:\n" + json.dumps(self.csv_mapping, indent=2)
                if unmapped:
                    output_text += f"\n\nSkipped/Unmapped: {len(unmapped)} fields"
                self.log(output_text)
                
                # IMMEDIATELY save mapping to workflow config file
                site_name = self.ent_site_name.get().strip()
                if site_name:
                    config_file = f'configs/{site_name}_workflow.json'
                    if os.path.exists(config_file):
                        try:
                            with open(config_file, 'r') as f:
                                config = json.load(f)
                            config['csv_mapping'] = self.csv_mapping
                            with open(config_file, 'w') as f:
                                json.dump(config, f, indent=2)
                            self.log(f"\n✓ Mapping saved to {config_file}")
                        except Exception as e:
                            self.log(f"Warning: Could not update config file: {e}")
                    else:
                        self.log("\nMapping saved to session. Click 'Save Config' to create workflow file.")
                else:
                    self.log("\nMapping saved to session. Enter Site Name and click 'Save Config' to persist.")
            else:
                self.log("Mapping cancelled or no fields were mapped.")
            
            save_prefs(self.collect_prefs())
        except Exception as e:
            self.log(f"Error mapping CSV: {e}")
            import traceback
            self.log(traceback.format_exc())

    def on_llm_settings(self):
        """Open LLM configuration dialog."""
        config = load_llm_config()
        
        # Create settings window
        settings_win = tk.Toplevel(self.root)
        settings_win.title("LLM Settings")
        settings_win.geometry("550x450")
        
        ttk.Label(settings_win, text="LLM Configuration for Intelligent Field Inference", font=('Arial', 12, 'bold')).pack(pady=10)
        
        # Enable checkbox
        enable_var = tk.BooleanVar(value=config.get('enabled', False))
        ttk.Checkbutton(settings_win, text="Enable LLM-powered field inference", variable=enable_var).pack(pady=5)
        
        # API Keys frame
        frm = ttk.Frame(settings_win, padding=10)
        frm.pack(fill='x', padx=10)
        
        ttk.Label(frm, text="OpenAI API Key:").grid(row=0, column=0, sticky='w', pady=5)
        api_key_var = tk.StringVar(value=config.get('api_key', ''))
        api_key_entry = ttk.Entry(frm, textvariable=api_key_var, width=40, show='*')
        api_key_entry.grid(row=0, column=1, pady=5, sticky='ew')
        frm.columnconfigure(1, weight=1)
        
        # Model
        ttk.Label(frm, text="Model:").grid(row=1, column=0, sticky='w', pady=5)
        model_var = tk.StringVar(value=config.get('model', 'gpt-4o-mini'))
        model_combo = ttk.Combobox(frm, textvariable=model_var, values=['gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo'], state='readonly', width=38)
        model_combo.grid(row=1, column=1, pady=5, sticky='ew')
        
        # Separator
        ttk.Separator(settings_win, orient='horizontal').pack(fill='x', pady=10, padx=10)
        
        # Web Research section
        ttk.Label(settings_win, text="Web Research (Optional)", font=('Arial', 10, 'bold')).pack(pady=5)
        
        enable_search_var = tk.BooleanVar(value=config.get('enable_search', False))
        ttk.Checkbutton(settings_win, text="Enable real-time web research for unmapped fields", variable=enable_search_var).pack(pady=5)
        
        # Tavily API Key
        frm2 = ttk.Frame(settings_win, padding=10)
        frm2.pack(fill='x', padx=10)
        ttk.Label(frm2, text="Tavily API Key:").grid(row=0, column=0, sticky='w', pady=5)
        search_key_var = tk.StringVar(value=config.get('search_api_key', ''))
        search_key_entry = ttk.Entry(frm2, textvariable=search_key_var, width=40, show='*')
        search_key_entry.grid(row=0, column=1, pady=5, sticky='ew')
        frm2.columnconfigure(1, weight=1)
        
        # Info
        info_text = (
            "LLM will intelligently fill unmapped form fields by analyzing your CSV data.\n\n"
            "With Web Research enabled:\n"
            "- AI searches the web using company name and field context\n"
            "- Finds accurate info like years in business, employee count, etc.\n"
            "- Uses real-time data to make informed decisions\n\n"
            "Get Tavily API key (free tier available): https://tavily.com"
        )
        info_label = ttk.Label(settings_win, text=info_text, wraplength=500, justify='left', foreground='gray', font=('Arial', 9))
        info_label.pack(pady=10, padx=10)
        
        def save_settings():
            try:
                new_config = {
                    'enabled': enable_var.get(),
                    'api_key': api_key_var.get().strip(),
                    'model': model_var.get(),
                    'base_url': None,
                    'enable_search': enable_search_var.get(),
                    'search_api_key': search_key_var.get().strip()
                }
                save_llm_config(new_config)
                status = f"LLM Settings saved. Enabled: {new_config['enabled']}, Model: {new_config['model']}"
                if new_config['enable_search']:
                    status += ", Web Research: ON"
                self.log(status)
                settings_win.destroy()
            except Exception as e:
                messagebox.showerror("Save Error", f"Failed to save settings: {e}")
                settings_win.destroy()  # Close anyway
        
        # Buttons
        btn_frame = ttk.Frame(settings_win)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Save", command=save_settings).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=settings_win.destroy).pack(side='left', padx=5)
        
        settings_win.transient(self.root)
        settings_win.grab_set()

    def on_save_config(self):
        site_name = self.ent_site_name.get().strip()
        site_url = self.ent_site_url.get().strip()
        if not site_name or not site_url:
            self.log("Please enter Site Name and Site URL at minimum.")
            return
        
        creds = None
        if self.ent_username.get().strip() and self.ent_password.get().strip():
            creds = {'username': self.ent_username.get().strip(), 'password': self.ent_password.get().strip()}

        try:
            # Allow saving partial configs (actions and mapping may be empty)
            filename = save_config(site_name, site_url, creds, self.actions_log or [], self.csv_mapping)
            self.log(f"Configuration saved to {filename}")
            if not self.csv_mapping:
                self.log("Note: No CSV mapping saved yet. Run 'Detect Fields' and 'Map CSV' to complete setup.")
            save_prefs(self.collect_prefs())
        except Exception as e:
            self.log(f"Error saving config: {e}")

    def on_edit_workflow(self):
        """Open workflow editor to modify CSV mappings and settings."""
        site_name = self.ent_site_name.get().strip()
        csv_file = self.ent_csv.get().strip()
        
        if not site_name:
            messagebox.showwarning("Missing Site Name", "Please enter a Site Name.")
            return
        
        if not csv_file:
            messagebox.showwarning("Missing CSV", "Please select a CSV file.")
            return
        
        config_file = f'configs/{site_name}_workflow.json'
        if not os.path.exists(config_file):
            messagebox.showwarning("No Workflow", f"No workflow found for '{site_name}'.\n\nPlease create and verify a workflow first.")
            return
        
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            
            self.log("Opening workflow editor...")
            
            # Open edit dialog
            editor = WorkflowEditorDialog(self.root, config, csv_file, self.log)
            updated_config = editor.get_result()
            
            if updated_config:
                # Save updated config
                with open(config_file, 'w') as f:
                    json.dump(updated_config, f, indent=2)
                
                # Update local state
                self.csv_mapping = updated_config.get('csv_mapping', {})
                self.actions_log = updated_config.get('actions', [])
                
                self.log("✓ Workflow updated successfully!")
            else:
                self.log("Workflow editing cancelled.")
                
        except Exception as e:
            self.log(f"Error opening workflow editor: {e}")
            import traceback
            self.log(traceback.format_exc())
    
    def on_verify_workflow(self):
        """Open step-by-step verification dialog."""
        site_name = self.ent_site_name.get().strip()
        csv_file = self.ent_csv.get().strip()
        
        if not site_name:
            self.log("Please enter Site Name.")
            return
        
        if not csv_file:
            self.log("Please select a CSV file.")
            return
        
        config_file = f'configs/{site_name}_workflow.json'
        if not os.path.exists(config_file):
            self.log(f"Workflow config not found. Please save config first.")
            return
        
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            
            self.log("Starting workflow verification...")
            self.log("A browser will open to test each step with the first CSV row.")
            self.log("Review each action and approve or correct as needed.\n")
            
            # Start verification dialog (pass log function for output)
            verifier = VerificationDialog(self.root, config, csv_file, main_log_func=self.log)
            approved, verified_actions = verifier.start_verification()
            
            if approved:
                self.log(f"✓ Workflow verified successfully! {len(verified_actions)} steps approved.")
                self.log("You can now click 'Run Workflow' to execute on all CSV rows.")
            else:
                self.log("Verification cancelled or incomplete.")
                
        except Exception as e:
            self.log(f"Verification error: {e}")
            import traceback
            self.log(traceback.format_exc())

    def on_run_workflow(self):
        site_name = self.ent_site_name.get().strip()
        csv_file = self.ent_csv.get().strip()
        if not csv_file:
            self.log("Please select a CSV file.")
            return
        config_file = f'configs/{site_name}_workflow.json'
        if not os.path.exists(config_file):
            self.log("Please save workflow config first.")
            return
        try:
            self.log(f"Running workflow (HTTP mode) for CSV {csv_file}...")
            replay_workflow_http(config_file, csv_file)
            self.log("Workflow completed successfully (HTTP mode).")
            save_prefs(self.collect_prefs())
        except Exception as e:
            self.log(f"Error running workflow: {e}")

    def on_run_workflow_browser(self):
        site_name = self.ent_site_name.get().strip()
        csv_file = self.ent_csv.get().strip()
        if not site_name or not csv_file:
            self.log("Please provide Site Name and CSV file.")
            return
        
        config_file = f'configs/{site_name}_workflow.json'
        if not os.path.exists(config_file):
            self.log(f"Config file not found: {config_file}")
            return
        
        # Check if workflow has been verified
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            
            if not config.get('verification_complete', False):
                response = messagebox.askyesno(
                    "Workflow Not Verified",
                    "This workflow has not been verified yet.\n\n"
                    "It's recommended to verify the workflow first to ensure:\n"
                    "- All steps work correctly\n"
                    "- Field mappings are accurate\n"
                    "- LLM suggestions are appropriate\n\n"
                    "Do you want to run it anyway?"
                )
                if not response:
                    self.log("Run cancelled. Please verify workflow first.")
                    return
        except Exception:
            pass
        
        self.log(f"Running workflow in browser mode for {csv_file}...")
        try:
            replay_workflow(config_file, csv_file, headless=False)
            self.log("Workflow execution completed.")
        except Exception as e:
            self.log(f"Error running workflow: {e}")

    def on_close(self):
        try:
            save_prefs(self.collect_prefs())
        finally:
            self.root.destroy()


def main():
    root = tk.Tk()
    app = LeadAutomationApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
