# lead_automation_tool_full.py

import os
import sys
import time
import json
import threading
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
    """Save LLM configuration to configs/llm_config.json."""
    try:
        config_path = os.path.join('configs', 'llm_config.json')
        os.makedirs('configs', exist_ok=True)
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving LLM config: {e}")

def load_processing_status(site_name):
    """Load processing status for CSV rows."""
    try:
        status_path = os.path.join('configs', f'{site_name}_processing_status.json')
        if os.path.exists(status_path):
            with open(status_path, 'r') as f:
                return json.load(f)
        return {}
    except Exception as e:
        print(f"Error loading processing status: {e}")
        return {}

def save_processing_status(site_name, status):
    """Save processing status for CSV rows."""
    try:
        status_path = os.path.join('configs', f'{site_name}_processing_status.json')
        os.makedirs('configs', exist_ok=True)
        with open(status_path, 'w') as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        print(f"Error saving processing status: {e}")

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
        
        # SPECIAL CASE: Employee count fields - just return 1 if blank
        field_lower = field_id.lower()
        csv_value = csv_row_data.get(field_id, '')
        is_blank = pd.isna(csv_value) or not str(csv_value).strip()
        
        if is_blank and ('employee' in field_lower or 'staff' in field_lower or 'workforce' in field_lower):
            return '1'
        
        # Perform web research if enabled
        research_results = None
        if config.get('enable_search') and config.get('search_api_key'):
            # Build search query from CSV data
            search_terms = []
            company_name = None
            
            # Extract company/business name from CSV
            for col, val in csv_row_data.items():
                if val and str(val).strip():
                    col_lower = col.lower()
                    if col_lower in ['name', 'company', 'company name', 'business', 'business name', 'organization']:
                        company_name = str(val)
                        search_terms.append(company_name)
                        break
            
            # Determine if we need to research this field
            should_research = False
            field_lower = field_id.lower()
            
            # Check if current field's CSV data is blank/missing
            csv_value = csv_row_data.get(field_id, '')
            is_blank = pd.isna(csv_value) or not str(csv_value).strip()
            
            # CRITICAL: Only research for specific field types when blank
            # DO NOT research for email, username, password, employee count, etc.
            researchable_fields = ['revenue', 'income', 'industry', 'sector']
            
            if is_blank and company_name:
                # Check if this field type should be researched
                for keyword in researchable_fields:
                    if keyword in field_lower:
                        should_research = True
                        break
            
            if should_research and company_name:
                # Build targeted search query based on field type
                if 'employee' in field_lower or 'staff' in field_lower or 'workforce' in field_lower:
                    search_query = f"{company_name} number of employees company size"
                elif 'revenue' in field_lower or 'income' in field_lower:
                    search_query = f"{company_name} annual revenue company financials"
                elif 'industry' in field_lower or 'sector' in field_lower:
                    search_query = f"{company_name} industry sector business type"
                else:
                    search_query = f"{company_name} {field_id.replace('_', ' ')}"
                
                print(f"    Researching: {search_query}")
                research_results = perform_web_research(search_query, config['search_api_key'])
        
        # Build detailed context
        context = f"""CONTEXT: You are filling out a web form with lead/business data. This is part of an automated workflow that processes CSV data row-by-row.

FORM FIELD BEING FILLED:
- Field ID: {field_id}
- Field Type: {field_type}
- Field Tag: {field_tag}
- Purpose: This field expects data related to '{field_id}'

RULES:
1. For STATE fields: ALWAYS use 2-letter abbreviations (e.g., CO not Colorado, NY not New York)
2. For blank/empty CSV values WITHOUT research: Return nothing (leave completely blank)
3. For employee count WITH research: Extract and return ONLY a number between 1-30
4. Match the format expected by the field type
5. Use exact data from CSV when available
6. When returning empty values: Output absolutely nothing, not the word 'empty' or 'blank' or 'none'

BUSINESS DATA FROM CSV:
"""
        for col, val in csv_row_data.items():
            if pd.isna(val) or val == '':
                context += f"- {col}: [BLANK - needs research]\n"
            else:
                context += f"- {col}: {val}\n"
        
        if research_results:
            context += f"\n=== WEB RESEARCH RESULTS ===\n{research_results}\n"
            context += "\nIMPORTANT: The field's CSV data is BLANK. Extract the answer from the web research above.\n"
        
        if available_options:
            context += f"\n=== AVAILABLE DROPDOWN OPTIONS ===\n"
            for opt in available_options:
                context += f"- {opt}\n"
            context += "\nTASK: Select the EXACT option text that best matches. Consider state abbreviations if this is a state field."
        else:
            context += "\nTASK: Return ONLY the value to enter. No explanation, no quotes, just the raw value."
        
        # Call OpenAI API
        client = OpenAI(api_key=config['api_key'], base_url=config.get('base_url'))
        
        system_prompt = """You are a form-filling assistant for automated lead processing workflows.

KEY RULES:
- For US states: ALWAYS use 2-letter codes (CO, NY, CA, etc.) never full names
- Match exact format of dropdown options when provided
- When CSV data is BLANK and NO research: Return absolutely nothing (blank output)
- For employee count WITH research: Return ONLY a number between 1-30 (e.g., "15" or "8")
- For revenue: Extract from "$5M revenue" (return just number like "5000000")
- Be precise and concise - return only the value, nothing else
- NEVER output words like "empty", "blank", "none", "null" - just leave it empty
- Consider the field ID/name to understand what data is expected"""
        
        response = client.chat.completions.create(
            model=config.get('model', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ],
            temperature=0.2,
            max_tokens=150
        )
        
        suggested_value = response.choices[0].message.content.strip()
        
        # Post-process: Clean up common LLM mistakes
        if suggested_value.lower() in ['empty string', 'empty', 'blank', 'none', 'null', 'n/a', 'not available']:
            return ''
        
        # For employee count fields: Extract just the number
        if 'employee' in field_id.lower() or 'staff' in field_id.lower():
            # Extract first number found
            import re
            numbers = re.findall(r'\d+', suggested_value)
            if numbers:
                num = int(numbers[0])
                # Clamp to 1-30 range
                suggested_value = str(min(max(num, 1), 30))
        
        # Remove quotes if LLM added them
        suggested_value = suggested_value.strip('"\'\'"')
        
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
        
        # Loop configuration removed - always runs full workflow for each row
        
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
        ttk.Label(header_frame, text="Start 2nd+ Rows", width=12, font=('Arial', 10, 'bold')).grid(row=0, column=4, padx=5)
        ttk.Label(header_frame, text="Delete", width=8, font=('Arial', 10, 'bold')).grid(row=0, column=5, padx=5)
        
        # Store comboboxes and deletion tracking
        self.mapping_combos = {}
        self.deleted_indices = set()
        self.step_frames = {}
        self.loop_start_var = tk.IntVar(value=config.get('loop_start_step', 0))
        
        # Create row for each action
        actions = config.get('actions', [])
        csv_mapping = config.get('csv_mapping', {})
        
        for i, action in enumerate(actions, 1):
            action_type = action.get('action', 'unknown')
            selector = action.get('selector', '')
            step_name = action.get('step_name', '')
            
            row_frame = ttk.Frame(scrollable_frame, relief='solid', borderwidth=1)
            row_frame.pack(fill='x', padx=5, pady=2)
            
            # Store frame reference
            step_idx = i - 1  # 0-based index
            self.step_frames[step_idx] = row_frame
            
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
            
            # Loop start radio button
            radio = ttk.Radiobutton(row_frame, variable=self.loop_start_var, value=step_idx)
            radio.grid(row=0, column=4, padx=5, pady=5)
            
            # Delete button
            delete_btn = ttk.Button(row_frame, text="✗ Delete", width=8, command=lambda idx=step_idx: self.delete_step(idx))
            delete_btn.grid(row=0, column=5, padx=5, pady=5)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Buttons
        btn_frame = ttk.Frame(self.window)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Save Changes", command=self.save_changes).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.cancel).pack(side='left', padx=5)
        
        self.window.transient(parent)
        self.window.grab_set()
        self.result_config = None
    
    def delete_step(self, step_idx):
        """Mark a step for deletion and hide it from UI."""
        self.deleted_indices.add(step_idx)
        if step_idx in self.step_frames:
            self.step_frames[step_idx].pack_forget()
        if self.main_log:
            self.main_log(f"[EDIT] Marked step {step_idx + 1} for deletion")
    
    def save_changes(self):
        """Save changes to config."""
        new_mapping = {}
        for selector, combo in self.mapping_combos.items():
            value = combo.get()
            if value == '(use recorded value)':
                new_mapping[selector] = '__RECORDED__'
            elif value != '(skip)':
                new_mapping[selector] = value
        
        # Filter out deleted steps
        original_actions = self.config.get('actions', [])
        filtered_actions = [action for idx, action in enumerate(original_actions) if idx not in self.deleted_indices]
        
        # Update config
        self.config['csv_mapping'] = new_mapping
        self.config['actions'] = filtered_actions
        self.config['loop_start_step'] = self.loop_start_var.get()
        
        self.result_config = self.config
        
        if self.main_log:
            self.main_log(f"[EDIT] Updated CSV mappings: {len(new_mapping)} fields mapped")
            if self.deleted_indices:
                self.main_log(f"[EDIT] Deleted {len(self.deleted_indices)} steps: {sorted([i+1 for i in self.deleted_indices])}")
            loop_start = self.loop_start_var.get()
            if loop_start > 0:
                self.main_log(f"[EDIT] 2nd+ row iterations will start from step {loop_start + 1}")
        
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
        ttk.Button(element_frame, text="🔍 Find Dropdown", command=self.find_dropdown).pack(side='left', padx=5)
        ttk.Button(element_frame, text="⚙ Change to CLICK", command=self.convert_to_click).pack(side='left', padx=5)
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
        
        self.btn_insert = ttk.Button(btn_frame, text="➕ Insert Step After", command=self.insert_step)
        self.btn_insert.pack(side='left', padx=5)
        
        self.btn_keyboard = ttk.Button(btn_frame, text="⌨ Add Keyboard Action", command=self.add_keyboard_action)
        self.btn_keyboard.pack(side='left', padx=5)
        
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
        
        # Describe action with step name prominently displayed
        step_name_display = f"'{existing_name}'" if existing_name else "(no name)"
        
        if action_type == 'click':
            action_text = f"Action: Click element\nName: {step_name_display}\nSelector: {action.get('selector', 'N/A')[:60]}"
            self.action_label.config(text=action_text)
            self.data_label.config(text="")
            self.value_label.config(text="")
            self.reasoning_text.config(state='normal')
            self.reasoning_text.delete('1.0', 'end')
            self.reasoning_text.config(state='disabled')
            self.override_var.set("")
        elif action_type == 'navigate':
            action_text = f"Action: Navigate to\nName: {step_name_display}\nURL: {action.get('url', 'N/A')}"
            self.action_label.config(text=action_text)
            self.data_label.config(text="")
            self.value_label.config(text="")
            self.reasoning_text.config(state='normal')
            self.reasoning_text.delete('1.0', 'end')
            self.reasoning_text.config(state='disabled')
            self.override_var.set("")
        elif action_type == 'interactive_sequence':
            # Show interactive sequence details
            actions_list = action.get('actions', [])
            keyboard_count = sum(1 for a in actions_list if isinstance(a, dict) and a.get('type') == 'keyboard')
            click_count = sum(1 for a in actions_list if isinstance(a, dict) and a.get('type') == 'click')
            
            action_text = f"Action: INTERACTIVE SEQUENCE\nName: {step_name_display}\nActions: {keyboard_count} keys + {click_count} clicks"
            self.action_label.config(text=action_text)
            self.data_label.config(text="")
            self.value_label.config(text="")
            
            # Show sequence details in reasoning box
            self.reasoning_text.config(state='normal')
            self.reasoning_text.delete('1.0', 'end')
            self.reasoning_text.insert('1.0', "Recorded sequence:\n")
            for i, act in enumerate(actions_list[:20], 1):  # Show first 20
                if isinstance(act, dict):
                    if act.get('type') == 'keyboard':
                        self.reasoning_text.insert('end', f"{i}. Key: {act.get('key')}\n")
                    elif act.get('type') == 'click':
                        self.reasoning_text.insert('end', f"{i}. Click: {act.get('selector', 'N/A')[:30]}\n")
            if len(actions_list) > 20:
                self.reasoning_text.insert('end', f"... and {len(actions_list) - 20} more actions\n")
            self.reasoning_text.config(state='disabled')
            self.override_var.set("")
        elif action_type == 'keyboard':
            key = action.get('key', 'UNKNOWN')
            repeat = action.get('repeat', 1)
            action_text = f"Action: KEYBOARD\nName: {step_name_display}\nKey: {key}\nRepeat: {repeat}x"
            self.action_label.config(text=action_text)
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
            
            action_text = f"Action: {action_type.upper()}\nName: {step_name_display}\nField ID: {field_id}\nSelector: {selector[:50]}"
            self.action_label.config(text=action_text)
            
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
            # Handle blank/NaN values properly
            raw_value = self.test_row[csv_col]
            if pd.isna(raw_value):
                value = ''
            else:
                value = str(raw_value)
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
            
            # Execute the action (keyboard, click, navigate, input, select)
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
    
    def delete_step(self):
        """Delete the current step from workflow."""
        if not self.config or not self.config.get('actions'):
            messagebox.showinfo("No Steps", "No workflow steps to delete.")
            return
        
        # Check bounds
        if self.current_step >= len(self.config['actions']):
            messagebox.showinfo("No Step", "No step at current position to delete.")
            return
        
        action = self.config['actions'][self.current_step]
        action_type = action.get('action', 'unknown')
        action_desc = f"{action_type.upper()} step"
        
        response = messagebox.askyesno("Delete Step", f"Delete {action_desc} at position {self.current_step + 1}?")
        
        if response:
            # Store in deleted list for restore
            self.deleted_steps.append({
                'step': action,
                'position': self.current_step
            })
            
            # Remove from actions list
            del self.config['actions'][self.current_step]
            self.log(f"Step {self.current_step + 1} DELETED: {action_desc} (can restore)")
            
            # Auto-save after deletion
            self.save_verification_progress()
            
            # Show next step (or complete if that was the last one)
            self.show_step()
    
    def add_keyboard_action(self):
        """Record live keyboard actions from the browser."""
        keyboard_win = tk.Toplevel(self.window)
        keyboard_win.title("Record Keyboard Actions")
        keyboard_win.geometry("600x400")
        
        ttk.Label(keyboard_win, text="🎹 Record Keyboard Actions", font=('Arial', 14, 'bold')).pack(pady=10)
        
        info_label = ttk.Label(keyboard_win, text="Click 'Start Recording', then press keys in the browser.\nClick 'Stop Recording' when done.", font=('Arial', 10), foreground='blue')
        info_label.pack(pady=10)
        
        # Recorded keys display
        display_frame = ttk.Frame(keyboard_win, padding=10)
        display_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        ttk.Label(display_frame, text="Captured Keys:", font=('Arial', 10, 'bold')).pack(anchor='w')
        
        keys_text = tk.Text(display_frame, height=10, width=60, font=('Courier', 10))
        keys_text.pack(fill='both', expand=True, pady=5)
        keys_text.config(state='disabled')
        
        # Step name
        name_frame = ttk.Frame(keyboard_win, padding=10)
        name_frame.pack(fill='x', padx=10)
        
        ttk.Label(name_frame, text="Step Name:").pack(side='left', padx=5)
        name_var = tk.StringVar(value="Keyboard Navigation")
        name_entry = ttk.Entry(name_frame, textvariable=name_var, width=40)
        name_entry.pack(side='left', padx=5)
        
        # Tracking state
        recorded_keys = []
        recording = False
        
        def update_display():
            keys_text.config(state='normal')
            keys_text.delete('1.0', 'end')
            for i, action in enumerate(recorded_keys, 1):
                if isinstance(action, dict):
                    if action.get('type') == 'keyboard':
                        keys_text.insert('end', f"{i}. KEYBOARD: {action.get('key')}\n")
                    elif action.get('type') == 'click':
                        keys_text.insert('end', f"{i}. CLICK: {action.get('selector', 'unknown')[:40]}\n")
                else:
                    # Old format compatibility
                    keys_text.insert('end', f"{i}. {action}\n")
            keys_text.config(state='disabled')
        
        def record_keyboard():
            """Capture keyboard, clicks, and scroll from browser using JavaScript."""
            nonlocal recording
            
            # Inject JavaScript to capture all interactions
            capture_script = """
            window.capturedActions = [];
            
            // Keyboard listener - NO preventDefault so keys work normally
            window.keyListener = function(e) {
                let keyName = e.key;
                
                // Map special keys
                if (keyName === 'Tab') keyName = 'TAB';
                else if (keyName === 'Enter') keyName = 'ENTER';
                else if (keyName === ' ') keyName = 'SPACE';
                else if (keyName === 'ArrowDown') keyName = 'ARROW_DOWN';
                else if (keyName === 'ArrowUp') keyName = 'ARROW_UP';
                else if (keyName === 'ArrowLeft') keyName = 'ARROW_LEFT';
                else if (keyName === 'ArrowRight') keyName = 'ARROW_RIGHT';
                else if (keyName === 'Escape') keyName = 'ESCAPE';
                else if (keyName === 'Backspace') keyName = 'BACKSPACE';
                else if (keyName === 'Delete') keyName = 'DELETE';
                
                window.capturedActions.push({type: 'keyboard', key: keyName});
                // NO preventDefault - let keys work naturally!
            };
            
            // Click listener - capture element and position
            window.clickListener = function(e) {
                // Get CSS selector for clicked element
                function getSelector(el) {
                    if (el.id) return '#' + el.id;
                    let path = [];
                    while (el && el.nodeType === Node.ELEMENT_NODE) {
                        let selector = el.nodeName.toLowerCase();
                        if (el.className) {
                            let classes = el.className.trim().split(/\\s+/);
                            if (classes[0]) selector += '.' + classes[0];
                        }
                        path.unshift(selector);
                        if (path.length >= 3) break;
                        el = el.parentNode;
                    }
                    return path.join(' > ');
                }
                
                let selector = getSelector(e.target);
                window.capturedActions.push({
                    type: 'click',
                    selector: selector,
                    x: e.clientX,
                    y: e.clientY,
                    scrollX: window.scrollX,
                    scrollY: window.scrollY
                });
            };
            
            document.addEventListener('keydown', window.keyListener);
            document.addEventListener('click', window.clickListener, true);
            """
            
            try:
                self.driver.execute_script(capture_script)
                recording = True
                info_label.config(text="🔴 RECORDING - Interact with browser (keys work normally), then click Stop", foreground='red')
                start_btn.config(state='disabled')
                stop_btn.config(state='normal')
            except Exception as e:
                messagebox.showerror("Error", f"Failed to start recording: {e}")
        
        def stop_recording():
            """Stop capturing and retrieve all actions."""
            nonlocal recording, recorded_keys
            
            try:
                # Retrieve captured actions from browser
                captured = self.driver.execute_script("return window.capturedActions || [];")
                
                # Remove event listeners
                self.driver.execute_script("""
                if (window.keyListener) {
                    document.removeEventListener('keydown', window.keyListener);
                    delete window.keyListener;
                }
                if (window.clickListener) {
                    document.removeEventListener('click', window.clickListener, true);
                    delete window.clickListener;
                }
                delete window.capturedActions;
                """)
                
                recorded_keys = captured
                recording = False
                
                update_display()
                
                # Count actions
                keyboard_count = sum(1 for a in captured if isinstance(a, dict) and a.get('type') == 'keyboard')
                click_count = sum(1 for a in captured if isinstance(a, dict) and a.get('type') == 'click')
                
                info_label.config(text=f"✓ Captured {keyboard_count} keys + {click_count} clicks", foreground='green')
                start_btn.config(state='normal')
                stop_btn.config(state='disabled')
                save_btn.config(state='normal')
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to stop recording: {e}")
        
        def save_actions():
            """Save recorded keyboard and click actions to workflow."""
            if not recorded_keys:
                messagebox.showwarning("No Actions", "No actions were recorded.")
                return
            
            step_name = name_var.get().strip() or "Interactive Navigation"
            
            # Create new action with all captured interactions
            new_step = {
                'action': 'interactive_sequence',
                'actions': recorded_keys,  # List of keyboard and click actions
                'step_name': step_name,
            }
            
            # Insert after current step
            insert_position = self.current_step + 1
            self.config['actions'].insert(insert_position, new_step)
            
            keyboard_count = sum(1 for a in recorded_keys if isinstance(a, dict) and a.get('type') == 'keyboard')
            click_count = sum(1 for a in recorded_keys if isinstance(a, dict) and a.get('type') == 'click')
            
            self.log(f"Added interactive sequence: {keyboard_count} keys + {click_count} clicks at position {insert_position + 1}")
            self.save_verification_progress()
            
            keyboard_win.destroy()
            messagebox.showinfo("Success", f"Interactive sequence saved!\n{keyboard_count} keystrokes + {click_count} clicks\n\nContinue verification to see it.")
            self.current_step = insert_position
            self.show_step()
        
        # Control buttons
        btn_frame = ttk.Frame(keyboard_win)
        btn_frame.pack(pady=10)
        
        start_btn = ttk.Button(btn_frame, text="🔴 Start Recording", command=record_keyboard)
        start_btn.pack(side='left', padx=5)
        
        stop_btn = ttk.Button(btn_frame, text="⏹ Stop Recording", command=stop_recording, state='disabled')
        stop_btn.pack(side='left', padx=5)
        
        save_btn = ttk.Button(btn_frame, text="💾 Save Actions", command=save_actions, state='disabled')
        save_btn.pack(side='left', padx=5)
        
        ttk.Button(btn_frame, text="Cancel", command=keyboard_win.destroy).pack(side='left', padx=5)
        
        keyboard_win.transient(self.window)
        keyboard_win.grab_set()
    
    def insert_step(self):
        """Insert a new step after the current step."""
        # Create dialog for step insertion
        insert_win = tk.Toplevel(self.window)
        insert_win.title("Insert New Step")
        insert_win.geometry("500x300")
        
        ttk.Label(insert_win, text="Insert New Step After Current", font=('Arial', 12, 'bold')).pack(pady=10)
        
        # Step type selection
        type_frame = ttk.Frame(insert_win, padding=10)
        type_frame.pack(fill='x', padx=10, pady=5)
        
        ttk.Label(type_frame, text="Step Type:").grid(row=0, column=0, sticky='w', pady=5)
        step_type_var = tk.StringVar(value='click')
        type_combo = ttk.Combobox(type_frame, textvariable=step_type_var, values=['click', 'input', 'select', 'navigate'], state='readonly', width=20)
        type_combo.grid(row=0, column=1, pady=5, padx=5)
        
        # Step name
        ttk.Label(type_frame, text="Step Name:").grid(row=1, column=0, sticky='w', pady=5)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(type_frame, textvariable=name_var, width=30)
        name_entry.grid(row=1, column=1, pady=5, padx=5)
        
        # Instructions
        info_label = ttk.Label(
            insert_win,
            text="After inserting, use 'Override Element' to capture the element.\n"
                 "You can also set values and mappings as needed.",
            foreground='gray',
            justify='left'
        )
        info_label.pack(pady=10, padx=10)
        
        def on_insert():
            step_type = step_type_var.get()
            step_name = name_var.get().strip()
            
            if not step_name:
                messagebox.showwarning("Name Required", "Please enter a name for the new step.")
                return
            
            # Create new step
            new_step = {
                'action': step_type,
                'step_name': step_name,
                'by': 'CSS_SELECTOR',
                'selector': 'div:nth-of-type(1)',  # Placeholder - user will override
            }
            
            if step_type in ('input', 'select'):
                new_step['value'] = ''
                new_step['field_context'] = {'id': step_name}
            elif step_type == 'navigate':
                new_step['url'] = self.driver.current_url if self.driver else ''
            
            # Insert after current step
            insert_position = self.current_step + 1
            self.config['actions'].insert(insert_position, new_step)
            
            self.log(f"Inserted new {step_type.upper()} step '{step_name}' at position {insert_position + 1}")
            
            # Save progress
            self.save_verification_progress()
            
            # Move to the new step
            self.current_step = insert_position
            self.show_step()
            
            insert_win.destroy()
        
        btn_frame = ttk.Frame(insert_win)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Insert Step", command=on_insert).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=insert_win.destroy).pack(side='left', padx=5)
        
        insert_win.transient(self.window)
        insert_win.grab_set()
    
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
    
    def convert_to_click(self):
        """Convert current SELECT action to CLICK for custom dropdowns."""
        action = self.config['actions'][self.current_step]
        
        if action.get('action') != 'select':
            messagebox.showinfo("Not a Select", "This action is not a SELECT. Only SELECT actions can be converted to CLICK.")
            return
        
        response = messagebox.askyesno(
            "Convert to Click",
            "Convert this SELECT action to a CLICK action?\n\n"
            "This is useful for custom dropdowns that aren't native <select> elements.\n\n"
            "After converting, you can use Override Element to capture the actual clickable dropdown option."
        )
        
        if response:
            # Convert to click action
            action['action'] = 'click'
            action['original_action'] = 'select'  # Keep track
            self.config['actions'][self.current_step] = action
            
            self.log("Converted SELECT to CLICK action. Use Override Element to capture the correct element.")
            
            # Refresh display
            self.show_step()
    
    def log(self, msg):
        """Log message to status label and main output."""
        self.status_label.config(text=msg)
        if self.main_log:
            self.main_log(f"[VERIFY] {msg}")
    
    def find_dropdown(self):
        """Scan page for dropdown elements and let user select one."""
        try:
            # Scan page for all select elements
            script = """
            (function() {
                var selects = document.querySelectorAll('select');
                var results = [];
                
                selects.forEach(function(sel, index) {
                    // Generate selector (matching override_element logic)
                    function cssPath(el) {
                        // Never return html or body elements
                        if (!el || el.nodeName === 'HTML' || el.nodeName === 'BODY') {
                            return null;
                        }
                        
                        if (el.id && /^[a-zA-Z][a-zA-Z0-9_-]*$/.test(el.id)) {
                            return '#' + el.id;
                        }
                        
                        var path = [];
                        var maxDepth = 5;
                        var current = el;
                        
                        while (current && current.nodeType === Node.ELEMENT_NODE && path.length < maxDepth) {
                            var selector = current.nodeName.toLowerCase();
                            
                            // Stop at html/body, don't include them
                            if (selector === 'html' || selector === 'body') {
                                break;
                            }
                            
                            // Add class if present (first class only, if valid)
                            if (current.className && typeof current.className === 'string') {
                                var classes = current.className.trim().split(/\\s+/);
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
                    
                    // Get options
                    var options = [];
                    for (var i = 0; i < sel.options.length; i++) {
                        options.push(sel.options[i].text.trim());
                    }
                    
                    results.push({
                        index: index,
                        id: sel.id || '(no id)',
                        name: sel.name || '(no name)',
                        selector: cssPath(sel),
                        optionsCount: options.length,
                        options: options,
                        label: sel.labels && sel.labels.length > 0 ? sel.labels[0].textContent.trim() : '(no label)'
                    });
                });
                
                return results;
            })();
            """
            
            dropdowns = self.driver.execute_script(script)
            
            if not dropdowns:
                messagebox.showinfo("No Dropdowns Found", "No <select> dropdown elements were found on the current page.")
                return
            
            self.log(f"Found {len(dropdowns)} dropdown(s) on page")
            
            # Show selection dialog
            self.show_dropdown_selector(dropdowns)
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to find dropdowns: {e}")
            self.log(f"Error finding dropdowns: {e}")
    
    def show_dropdown_selector(self, dropdowns):
        """Show dialog to select which dropdown to use."""
        select_win = tk.Toplevel(self.window)
        select_win.title("Select Dropdown")
        select_win.geometry("800x500")
        
        ttk.Label(select_win, text=f"Found {len(dropdowns)} Dropdown(s) - Select One", font=('Arial', 12, 'bold')).pack(pady=10)
        
        # Scrollable list
        list_frame = ttk.Frame(select_win)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        canvas = tk.Canvas(list_frame)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        selected_dropdown = {'value': None}
        
        for dd in dropdowns:
            dd_frame = ttk.Frame(scrollable_frame, relief='solid', borderwidth=1, padding=10)
            dd_frame.pack(fill='x', padx=5, pady=5)
            
            # Info
            info_text = f"Dropdown #{dd['index'] + 1}\n"
            info_text += f"Label: {dd['label']}\n"
            info_text += f"ID: {dd['id']} | Name: {dd['name']}\n"
            info_text += f"Options: {dd['optionsCount']} items\n"
            info_text += f"Selector: {dd['selector'][:60]}..."
            
            ttk.Label(dd_frame, text=info_text, font=('Courier', 9)).pack(anchor='w')
            
            # Preview options
            preview = ", ".join(dd['options'][:5])
            if len(dd['options']) > 5:
                preview += f" ... (+{len(dd['options']) - 5} more)"
            ttk.Label(dd_frame, text=f"Preview: {preview}", foreground='gray').pack(anchor='w', pady=(5, 0))
            
            # Select button
            def make_select(dropdown):
                def select():
                    selected_dropdown['value'] = dropdown
                    select_win.destroy()
                return select
            
            ttk.Button(dd_frame, text="Use This Dropdown", command=make_select(dd)).pack(anchor='e', pady=(5, 0))
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        ttk.Button(select_win, text="Cancel", command=select_win.destroy).pack(pady=10)
        
        select_win.transient(self.window)
        select_win.grab_set()
        select_win.wait_window()
        
        # If user selected a dropdown, show options picker
        if selected_dropdown['value']:
            self.show_dropdown_options(selected_dropdown['value'])
    
    def show_dropdown_options(self, dropdown):
        """Show dialog to select value from dropdown options."""
        options_win = tk.Toplevel(self.window)
        options_win.title("Select Option")
        options_win.geometry("600x400")
        
        ttk.Label(options_win, text=f"Select Option from: {dropdown['label']}", font=('Arial', 12, 'bold')).pack(pady=10)
        ttk.Label(options_win, text=f"Selector: {dropdown['selector']}", font=('Courier', 9), foreground='gray').pack(pady=5)
        
        # Options list
        list_frame = ttk.Frame(options_win)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        canvas = tk.Canvas(list_frame)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        selected_option = {'value': None}
        
        for opt in dropdown['options']:
            if not opt.strip():
                continue
            
            opt_frame = ttk.Frame(scrollable_frame, relief='solid', borderwidth=1, padding=5)
            opt_frame.pack(fill='x', padx=5, pady=2)
            
            ttk.Label(opt_frame, text=opt, font=('Arial', 10)).pack(side='left', padx=5)
            
            def make_select_option(option):
                def select():
                    selected_option['value'] = option
                    options_win.destroy()
                return select
            
            ttk.Button(opt_frame, text="Select", command=make_select_option(opt)).pack(side='right', padx=5)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        ttk.Button(options_win, text="Cancel", command=options_win.destroy).pack(pady=10)
        
        options_win.transient(self.window)
        options_win.grab_set()
        options_win.wait_window()
        
        # If user selected an option, update the action
        if selected_option['value']:
            action = self.config['actions'][self.current_step]
            action['action'] = 'select'
            action['selector'] = dropdown['selector']
            action['by'] = 'CSS_SELECTOR'
            self.override_var.set(selected_option['value'])  # Set the selected value
            
            self.element_status.config(text=f"Dropdown configured: {dropdown['label']}", foreground='green')
            self.log(f"Configured dropdown: {dropdown['selector'][:60]} with value: {selected_option['value']}")
            
            # Refresh display
            self.show_step()
    
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
                    // Never return html or body elements
                    if (!el || el.nodeName === 'HTML' || el.nodeName === 'BODY') {
                        return null;
                    }
                    
                    if (el.id && /^[a-zA-Z][a-zA-Z0-9_-]*$/.test(el.id)) {
                        return '#' + el.id;
                    }
                    
                    var path = [];
                    var maxDepth = 5;
                    var current = el;
                    
                    while (current && current.nodeType === Node.ELEMENT_NODE && path.length < maxDepth) {
                        var selector = current.nodeName.toLowerCase();
                        
                        // Stop at html/body, don't include them
                        if (selector === 'html' || selector === 'body') {
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
                    
                    console.log('Click captured on:', target.tagName, target.className, target.id);
                    
                    // For SELECT elements, prevent dropdown from opening
                    if (target.tagName === 'SELECT') {
                        console.log('SELECT element clicked - capturing without opening dropdown');
                        // Force close if it tried to open
                        setTimeout(function() { target.blur(); }, 0);
                    }
                    
                    // For clicks on OPTION elements inside SELECT, use the SELECT instead
                    if (target.tagName === 'OPTION' && target.parentElement && target.parentElement.tagName === 'SELECT') {
                        target = target.parentElement;
                        console.log('Redirected from OPTION to SELECT:', target);
                    }
                    
                    // Only reject if truly clicking on html/body with no children at click point
                    if ((target.tagName === 'HTML' || target.tagName === 'BODY') && 
                        (!e.clientX || !e.clientY)) {
                        console.log('Rejected: clicked on page background');
                        alert('Please click on a specific element, not the page background.');
                        return false;
                    }
                    
                    // For BODY clicks with coordinates, try to find the actual element at that point
                    if (target.tagName === 'BODY' && e.clientX && e.clientY) {
                        var elementAtPoint = document.elementFromPoint(e.clientX, e.clientY);
                        if (elementAtPoint && elementAtPoint.tagName !== 'BODY' && elementAtPoint.tagName !== 'HTML') {
                            target = elementAtPoint;
                            console.log('Refined target to:', target.tagName, target.className, target.id);
                        }
                    }
                    
                    // If it's a link, button, or select, explicitly prevent default action
                    if (target.tagName === 'A' || target.tagName === 'BUTTON' || target.tagName === 'SELECT') {
                        e.preventDefault();
                    }
                    
                    var cssPathResult = cssPath(target);
                    if (!cssPathResult) {
                        console.log('Could not generate selector for:', target);
                        alert('Could not generate selector for this element. Try clicking a more specific element.');
                        return false;
                    }
                    
                    console.log('Captured element with selector:', cssPathResult);
                    
                    window._elementOverride = {
                        tag: target.tagName.toLowerCase(),
                        id: target.id || null,
                        name: target.name || null,
                        type: target.type || null,
                        cssPath: cssPathResult,
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
                                var classes = current.className.trim().split(/\\s+/);
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
                        
                        console.log('Click captured on:', target.tagName, target.className, target.id);
                        
                        // For SELECT elements, prevent dropdown from opening
                        if (target.tagName === 'SELECT') {
                            console.log('SELECT element clicked - capturing without opening dropdown');
                            setTimeout(function() { target.blur(); }, 0);
                        }
                        
                        // For clicks on OPTION elements inside SELECT, use the SELECT instead
                        if (target.tagName === 'OPTION' && target.parentElement && target.parentElement.tagName === 'SELECT') {
                            target = target.parentElement;
                            console.log('Redirected from OPTION to SELECT:', target);
                        }
                        
                        // Only reject if truly clicking on html/body with no children at click point
                        if ((target.tagName === 'HTML' || target.tagName === 'BODY') && 
                            (!e.clientX || !e.clientY)) {
                            console.log('Rejected: clicked on page background');
                            alert('Please click on a specific element, not the page background.');
                            return false;
                        }
                        
                        // For BODY clicks with coordinates, try to find the actual element at that point
                        if (target.tagName === 'BODY' && e.clientX && e.clientY) {
                            var elementAtPoint = document.elementFromPoint(e.clientX, e.clientY);
                            if (elementAtPoint && elementAtPoint.tagName !== 'BODY' && elementAtPoint.tagName !== 'HTML') {
                                target = elementAtPoint;
                                console.log('Refined target to:', target.tagName, target.className, target.id);
                            }
                        }
                        
                        // If it's a link, button, or select, explicitly prevent default action
                        if (target.tagName === 'A' || target.tagName === 'BUTTON' || target.tagName === 'SELECT') {
                            e.preventDefault();
                        }
                        
                        var cssPathResult = cssPath(target);
                        if (!cssPathResult) {
                            console.log('Could not generate selector for:', target);
                            alert('Could not generate selector for this element. Try clicking a more specific element.');
                            return false;
                        }
                        
                        console.log('Captured element with selector:', cssPathResult);
                        
                        window._elementOverride = {
                            tag: target.tagName.toLowerCase(),
                            id: target.id || null,
                            name: target.name || null,
                            type: target.type || null,
                            cssPath: cssPathResult,
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
                # User clicked an element! Now show hierarchy navigator
                self.log("Element captured. Opening hierarchy navigator...")
                self.element_status.config(text="Opening hierarchy navigator...", foreground='blue')
                
                # Open hierarchy navigator dialog
                final_selector = self.show_hierarchy_navigator(result.get('cssPath', ''))
                
                if final_selector:
                    # Update current action's selector
                    action = self.config['actions'][self.current_step]
                    action['selector'] = final_selector
                    action['by'] = 'CSS_SELECTOR'
                    self.config['actions'][self.current_step] = action
                    
                    self.element_status.config(text=f"Element overridden! New selector: {final_selector[:80]}...", foreground='green')
                    self.log(f"Element overridden! New selector: {final_selector[:80]}...")
                    
                    # Refresh display
                    self.show_step()
                else:
                    self.element_status.config(text="Element override cancelled", foreground='gray')
                    self.log("Element override cancelled")
                return
            
            # Continue polling
            self.window.after(100, self._check_element_override)
            
        except Exception as e:
            self.element_status.config(text="Polling error", foreground='red')
            self.log(f"Element override error: {e}")
    
    def show_hierarchy_navigator(self, initial_selector):
        """Show dialog to navigate DOM hierarchy and select exact element."""
        nav_win = tk.Toplevel(self.window)
        nav_win.title("DOM Hierarchy Navigator")
        nav_win.geometry("700x400")
        
        ttk.Label(nav_win, text="Navigate DOM Hierarchy", font=('Arial', 12, 'bold')).pack(pady=10)
        
        # Current selector state
        current_selector = {'value': initial_selector}
        
        # Element info display
        info_frame = ttk.LabelFrame(nav_win, text="Current Element", padding=10)
        info_frame.pack(fill='x', padx=10, pady=5)
        
        tag_label = ttk.Label(info_frame, text="Tag: ", font=('Courier', 10))
        tag_label.pack(anchor='w')
        
        id_label = ttk.Label(info_frame, text="ID: ", font=('Courier', 10))
        id_label.pack(anchor='w')
        
        classes_label = ttk.Label(info_frame, text="Classes: ", font=('Courier', 10))
        classes_label.pack(anchor='w')
        
        selector_label = ttk.Label(info_frame, text="Selector: ", font=('Courier', 9), wraplength=650)
        selector_label.pack(anchor='w', pady=5)
        
        # Navigation buttons
        nav_button_frame = ttk.Frame(nav_win)
        nav_button_frame.pack(pady=10)
        
        def update_display():
            """Update element info display and highlight."""
            try:
                # Get element info from browser
                script = f"""
                (function() {{
                    try {{
                        var el = document.querySelector('{current_selector['value']}');
                        if (!el) return null;
                        
                        return {{
                            tag: el.tagName.toLowerCase(),
                            id: el.id || '(none)',
                            classes: el.className || '(none)',
                            selector: '{current_selector['value']}',
                            hasParent: el.parentElement && el.parentElement.tagName !== 'HTML'
                        }};
                    }} catch(e) {{
                        return null;
                    }}
                }})();
                """
                
                info = self.driver.execute_script(script)
                
                if info:
                    tag_label.config(text=f"Tag: {info['tag']}")
                    id_label.config(text=f"ID: {info['id']}")
                    classes_label.config(text=f"Classes: {info['classes']}")
                    selector_label.config(text=f"Selector: {info['selector']}")
                    
                    # Highlight element
                    highlight_script = f"""
                    (function() {{
                        // Remove old highlights
                        var oldHighlights = document.querySelectorAll('[data-hierarchy-highlight]');
                        oldHighlights.forEach(function(el) {{
                            el.style.border = '';
                            el.style.backgroundColor = '';
                            el.style.outline = '';
                            el.removeAttribute('data-hierarchy-highlight');
                        }});
                        
                        // Add new highlight
                        var el = document.querySelector('{current_selector['value']}');
                        if (el) {{
                            el.style.border = '3px solid blue';
                            el.style.backgroundColor = 'rgba(0,0,255,0.1)';
                            el.style.outline = '2px dashed cyan';
                            el.setAttribute('data-hierarchy-highlight', 'true');
                            el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                        }}
                    }})();
                    """
                    self.driver.execute_script(highlight_script)
            except Exception as e:
                tag_label.config(text=f"Error: {str(e)[:50]}")
        
        def move_up():
            """Move to parent element."""
            try:
                script = f"""
                (function() {{
                    var el = document.querySelector('{current_selector['value']}');
                    if (!el || !el.parentElement || el.parentElement.tagName === 'HTML') return null;
                    
                    var parent = el.parentElement;
                    
                    // Generate selector for parent
                    function cssPath(el) {{
                        if (el.id && /^[a-zA-Z][a-zA-Z0-9_-]*$/.test(el.id)) {{
                            return '#' + el.id;
                        }}
                        
                        var path = [];
                        var current = el;
                        var maxDepth = 5;
                        
                        while (current && current.nodeType === Node.ELEMENT_NODE && path.length < maxDepth) {{
                            var selector = current.nodeName.toLowerCase();
                            
                            if (selector === 'html' || selector === 'body') {{
                                break;
                            }}
                            
                            if (current.className && typeof current.className === 'string') {{
                                var classes = current.className.trim().split(/\\s+/);
                                if (classes.length > 0 && /^[a-zA-Z_-][a-zA-Z0-9_-]*$/.test(classes[0])) {{
                                    selector += '.' + classes[0];
                                }}
                            }}
                            
                            var siblings = current.parentNode ? Array.from(current.parentNode.children) : [];
                            var sameTagSiblings = siblings.filter(function(s) {{ return s.nodeName === current.nodeName; }});
                            if (sameTagSiblings.length > 1) {{
                                var index = sameTagSiblings.indexOf(current) + 1;
                                selector += ':nth-of-type(' + index + ')';
                            }}
                            
                            path.unshift(selector);
                            current = current.parentNode;
                        }}
                        
                        return path.join(' > ');
                    }}
                    
                    return cssPath(parent);
                }})();
                """
                
                parent_selector = self.driver.execute_script(script)
                if parent_selector:
                    current_selector['value'] = parent_selector
                    update_display()
            except Exception as e:
                print(f"Move up error: {e}")
        
        def move_down():
            """Move to first child element."""
            try:
                script = f"""
                (function() {{
                    var el = document.querySelector('{current_selector['value']}');
                    if (!el || !el.children || el.children.length === 0) return null;
                    
                    var child = el.children[0];
                    
                    function cssPath(el) {{
                        if (el.id && /^[a-zA-Z][a-zA-Z0-9_-]*$/.test(el.id)) {{
                            return '#' + el.id;
                        }}
                        
                        var path = [];
                        var current = el;
                        var maxDepth = 5;
                        
                        while (current && current.nodeType === Node.ELEMENT_NODE && path.length < maxDepth) {{
                            var selector = current.nodeName.toLowerCase();
                            
                            if (selector === 'html' || selector === 'body') {{
                                break;
                            }}
                            
                            if (current.className && typeof current.className === 'string') {{
                                var classes = current.className.trim().split(/\\s+/);
                                if (classes.length > 0 && /^[a-zA-Z_-][a-zA-Z0-9_-]*$/.test(classes[0])) {{
                                    selector += '.' + classes[0];
                                }}
                            }}
                            
                            var siblings = current.parentNode ? Array.from(current.parentNode.children) : [];
                            var sameTagSiblings = siblings.filter(function(s) {{ return s.nodeName === current.nodeName; }});
                            if (sameTagSiblings.length > 1) {{
                                var index = sameTagSiblings.indexOf(current) + 1;
                                selector += ':nth-of-type(' + index + ')';
                            }}
                            
                            path.unshift(selector);
                            current = current.parentNode;
                        }}
                        
                        return path.join(' > ');
                    }}
                    
                    return cssPath(child);
                }})();
                """
                
                child_selector = self.driver.execute_script(script)
                if child_selector:
                    current_selector['value'] = child_selector
                    update_display()
            except Exception as e:
                print(f"Move down error: {e}")
        
        ttk.Button(nav_button_frame, text="↑ Up (Parent)", command=move_up, width=15).pack(side='left', padx=5)
        ttk.Button(nav_button_frame, text="↓ Down (First Child)", command=move_down, width=15).pack(side='left', padx=5)
        
        # Instructions
        ttk.Label(nav_win, text="Use ↑ Up to select parent element, ↓ Down to select first child.\nClick 'Use This Element' when ready.", foreground='gray').pack(pady=5)
        
        # Confirm/Cancel buttons
        final_selector = {'value': None}
        
        def confirm():
            final_selector['value'] = current_selector['value']
            # Remove highlight
            try:
                self.driver.execute_script("""
                var oldHighlights = document.querySelectorAll('[data-hierarchy-highlight]');
                oldHighlights.forEach(function(el) {
                    el.style.border = '';
                    el.style.backgroundColor = '';
                    el.style.outline = '';
                    el.removeAttribute('data-hierarchy-highlight');
                });
                """)
            except:
                pass
            nav_win.destroy()
        
        def cancel():
            # Remove highlight
            try:
                self.driver.execute_script("""
                var oldHighlights = document.querySelectorAll('[data-hierarchy-highlight]');
                oldHighlights.forEach(function(el) {
                    el.style.border = '';
                    el.style.backgroundColor = '';
                    el.style.outline = '';
                    el.removeAttribute('data-hierarchy-highlight');
                });
                """)
            except:
                pass
            nav_win.destroy()
        
        btn_frame = ttk.Frame(nav_win)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="✓ Use This Element", command=confirm).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=cancel).pack(side='left', padx=5)
        
        # Initial display
        update_display()
        
        nav_win.transient(self.window)
        nav_win.grab_set()
        nav_win.wait_window()
        
        return final_selector['value']
    
    def execute_action(self, action):
        """Execute a single action in the browser."""
        action_type = action.get('action')
        by = getattr(By, action.get('by', 'CSS_SELECTOR').upper()) if action.get('by') else By.CSS_SELECTOR
        selector = action.get('selector')
        
        if action_type == 'navigate':
            self.driver.get(action.get('url'))
            time.sleep(1)
        elif action_type == 'keyboard':
            # Execute keyboard action
            from selenium.webdriver.common.keys import Keys
            
            # Map key names to Selenium Keys
            key_map = {
                'TAB': Keys.TAB,
                'ENTER': Keys.ENTER,
                'SPACE': Keys.SPACE,
                'ARROW_DOWN': Keys.ARROW_DOWN,
                'ARROW_UP': Keys.ARROW_UP,
                'ARROW_LEFT': Keys.ARROW_LEFT,
                'ARROW_RIGHT': Keys.ARROW_RIGHT,
                'ESCAPE': Keys.ESCAPE,
                'BACKSPACE': Keys.BACKSPACE,
                'DELETE': Keys.DELETE,
            }
            
            # Check if new format (keys array) or old format (single key + repeat)
            if 'keys' in action:
                # New format: list of keys to press in sequence
                keys_sequence = action.get('keys', [])
                try:
                    active_element = self.driver.switch_to.active_element
                    for key_name in keys_sequence:
                        selenium_key = key_map.get(key_name, Keys.TAB)
                        active_element.send_keys(selenium_key)
                        time.sleep(0.3)
                except Exception as e:
                    # Fallback: send to body
                    body = self.driver.find_element(By.TAG_NAME, 'body')
                    for key_name in keys_sequence:
                        selenium_key = key_map.get(key_name, Keys.TAB)
                        body.send_keys(selenium_key)
                        time.sleep(0.3)
            else:
                # Old format: single key with repeat count
                key_name = action.get('key', 'TAB')
                repeat = action.get('repeat', 1)
                selenium_key = key_map.get(key_name, Keys.TAB)
                
                try:
                    active_element = self.driver.switch_to.active_element
                    for _ in range(repeat):
                        active_element.send_keys(selenium_key)
                        time.sleep(0.3)
                except Exception as e:
                    # Fallback: send to body
                    body = self.driver.find_element(By.TAG_NAME, 'body')
                    for _ in range(repeat):
                        body.send_keys(selenium_key)
                        time.sleep(0.3)
            
            time.sleep(0.5)  # Wait for page to respond
        elif action_type == 'interactive_sequence':
            # Execute sequence of keyboard + click actions
            from selenium.webdriver.common.keys import Keys
            
            key_map = {
                'TAB': Keys.TAB, 'ENTER': Keys.ENTER, 'SPACE': Keys.SPACE,
                'ARROW_DOWN': Keys.ARROW_DOWN, 'ARROW_UP': Keys.ARROW_UP,
                'ARROW_LEFT': Keys.ARROW_LEFT, 'ARROW_RIGHT': Keys.ARROW_RIGHT,
                'ESCAPE': Keys.ESCAPE, 'BACKSPACE': Keys.BACKSPACE, 'DELETE': Keys.DELETE,
            }
            
            actions_list = action.get('actions', [])
            for act in actions_list:
                if isinstance(act, dict):
                    if act.get('type') == 'keyboard':
                        key_name = act.get('key')
                        selenium_key = key_map.get(key_name, Keys.TAB)
                        try:
                            active_element = self.driver.switch_to.active_element
                            active_element.send_keys(selenium_key)
                        except:
                            body = self.driver.find_element(By.TAG_NAME, 'body')
                            body.send_keys(selenium_key)
                        time.sleep(0.3)
                    elif act.get('type') == 'click':
                        selector = act.get('selector')
                        if selector:
                            try:
                                # Scroll to position if provided
                                scroll_y = act.get('scrollY', 0)
                                if scroll_y:
                                    self.driver.execute_script(f"window.scrollTo(0, {scroll_y});")
                                    time.sleep(0.2)
                                
                                el = WebDriverWait(self.driver, 5).until(
                                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                                )
                                el.click()
                                time.sleep(0.5)
                            except Exception as e:
                                self.log(f"Click failed on {selector}: {e}")
            time.sleep(0.5)
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
            # IMPORTANT: Reload current config to preserve any CSV mapping changes
            site_name = self.config.get('site_name', 'workflow')
            config_file = f'configs/{site_name}_workflow.json'
            
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    current_config = json.load(f)
            else:
                current_config = self.config.copy()
            
            # Update ONLY the actions and deleted steps, preserve everything else
            current_config['actions'] = self.verified_actions
            current_config['deleted_steps'] = self.deleted_steps
            current_config['verification_complete'] = False
            
            filename = f'configs/{site_name}_verified_partial.json'
            with open(filename, 'w') as f:
                json.dump(current_config, f, indent=2)
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
            # IMPORTANT: Reload current config to preserve any CSV mapping changes
            site_name = self.config.get('site_name', 'workflow')
            filename = f'configs/{site_name}_workflow.json'
            
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    verified_config = json.load(f)
            else:
                verified_config = self.config.copy()
            
            # Update ONLY verified actions, preserve CSV mappings and other metadata
            verified_config['actions'] = self.verified_actions
            verified_config['verification_complete'] = True
            
            # Save deleted steps if user chose to keep them
            if self.deleted_steps:
                verified_config['deleted_steps_archive'] = self.deleted_steps
                self.log(f"Saved {len(self.deleted_steps)} deleted steps to archive")
            
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
        
        # Save progress even if cancelled (for automation to use)
        if self.verified_actions:
            try:
                site_name = self.config.get('site_name', 'workflow')
                filename = f'configs/{site_name}_workflow.json'
                
                # IMPORTANT: Reload current config to preserve CSV mappings
                if os.path.exists(filename):
                    with open(filename, 'r') as f:
                        verified_config = json.load(f)
                else:
                    verified_config = self.config.copy()
                
                # Update ONLY verified actions, preserve CSV mappings and other metadata
                verified_config['actions'] = self.verified_actions
                verified_config['deleted_steps'] = self.deleted_steps
                verified_config['verification_complete'] = False  # Mark as incomplete
                
                with open(filename, 'w') as f:
                    json.dump(verified_config, f, indent=2)
                self.log(f"Saved {len(self.verified_actions)} verified steps for automation use.")
            except Exception as e:
                self.log(f"Warning: Failed to save progress: {e}")
        
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

def replay_workflow_single_row(driver, config, row, log_callback=None, row_idx=0, session_iteration=1):
    """Replay workflow for a single row with an already-initialized driver.
    
    Args:
        session_iteration: Which iteration in THIS browser session (1=first, 2=second, etc)
    """
    csv_row_dict = row.to_dict()
    
    # US State name to abbreviation mapping
    STATE_MAPPING = {
        'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA',
        'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE', 'florida': 'FL', 'georgia': 'GA',
        'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA',
        'kansas': 'KS', 'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
        'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO',
        'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ',
        'new mexico': 'NM', 'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH',
        'oklahoma': 'OK', 'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
        'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT',
        'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY'
    }
    
    # Determine which steps to execute
    loop_start = config.get('loop_start_step', 0)
    actions_to_execute = config['actions']
    start_idx = 0
    
    if session_iteration > 1 and loop_start > 0:
        # On 2nd+ iterations IN THIS SESSION, start from loop point
        start_idx = loop_start
        actions_to_execute = config['actions'][loop_start:]
        if log_callback:
            log_callback(f"Starting from step {loop_start + 1} (skipping login/setup steps)")
    
    for step_idx, action in enumerate(actions_to_execute, start=start_idx):
        try:
            action_type = action.get('action')
            step_name = action.get('step_name', '')
            
            # Log step
            step_desc = step_name if step_name else f"{action_type.upper() if action_type else 'Unknown'} action"
            if log_callback:
                log_callback(f"Step {step_idx + 1}: {step_desc}")
            
            if action_type == 'interactive_sequence':
                # Handle interactive sequence (keyboard + clicks)
                from selenium.webdriver.common.keys import Keys
                
                key_map = {
                    'TAB': Keys.TAB, 'ENTER': Keys.ENTER, 'SPACE': Keys.SPACE,
                    'ARROW_DOWN': Keys.ARROW_DOWN, 'ARROW_UP': Keys.ARROW_UP,
                    'ARROW_LEFT': Keys.ARROW_LEFT, 'ARROW_RIGHT': Keys.ARROW_RIGHT,
                    'ESCAPE': Keys.ESCAPE, 'BACKSPACE': Keys.BACKSPACE, 'DELETE': Keys.DELETE,
                }
                
                actions_list = action.get('actions', [])
                for act in actions_list:
                    if isinstance(act, dict):
                        if act.get('type') == 'keyboard':
                            key_name = act.get('key')
                            selenium_key = key_map.get(key_name, Keys.TAB)
                            try:
                                active_element = driver.switch_to.active_element
                                active_element.send_keys(selenium_key)
                            except:
                                body = driver.find_element(By.TAG_NAME, 'body')
                                body.send_keys(selenium_key)
                            time.sleep(0.3)
                        elif act.get('type') == 'click':
                            selector = act.get('selector')
                            if selector:
                                try:
                                    scroll_y = act.get('scrollY', 0)
                                    if scroll_y:
                                        driver.execute_script(f"window.scrollTo(0, {scroll_y});")
                                        time.sleep(0.2)
                                    
                                    el = WebDriverWait(driver, 5).until(
                                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                                    )
                                    el.click()
                                    time.sleep(0.5)
                                except Exception as e:
                                    if log_callback:
                                        log_callback(f"Click failed: {e}")
                time.sleep(0.5)
                continue
            
            if action_type == 'keyboard':
                # Handle keyboard actions
                from selenium.webdriver.common.keys import Keys
                
                key_map = {
                    'TAB': Keys.TAB,
                    'ENTER': Keys.ENTER,
                    'SPACE': Keys.SPACE,
                    'ARROW_DOWN': Keys.ARROW_DOWN,
                    'ARROW_UP': Keys.ARROW_UP,
                    'ARROW_LEFT': Keys.ARROW_LEFT,
                    'ARROW_RIGHT': Keys.ARROW_RIGHT,
                    'ESCAPE': Keys.ESCAPE,
                    'BACKSPACE': Keys.BACKSPACE,
                    'DELETE': Keys.DELETE,
                }
                
                # Check if new format (keys array) or old format (single key + repeat)
                if 'keys' in action:
                    # New format: list of keys recorded from browser
                    keys_sequence = action.get('keys', [])
                    active_element = driver.switch_to.active_element
                    for key_name in keys_sequence:
                        selenium_key = key_map.get(key_name, Keys.TAB)
                        active_element.send_keys(selenium_key)
                        time.sleep(0.3)
                else:
                    # Old format: single key with repeat count
                    key_name = action.get('key', 'TAB')
                    repeat = action.get('repeat', 1)
                    selenium_key = key_map.get(key_name, Keys.TAB)
                    active_element = driver.switch_to.active_element
                    for _ in range(repeat):
                        active_element.send_keys(selenium_key)
                        time.sleep(0.3)
                
                time.sleep(0.5)
                continue
            
            by = getattr(By, action.get('by', 'CSS_SELECTOR').upper())
            selector = action.get('selector')
            if not selector and action_type != 'navigate':
                continue
            
            if action_type == 'navigate':
                nav_url = action.get('url')
                if nav_url:
                    driver.get(nav_url)
                    time.sleep(1)
                continue
            
            if action_type == 'click':
                el = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((by, selector))
                )
                time.sleep(0.5)
                
                # Click with stale element retry (same as verify workflow)
                try:
                    el.click()
                except Exception as e:
                    if 'stale' in str(e).lower():
                        if log_callback:
                            log_callback("Element became stale, re-finding...")
                        el = WebDriverWait(driver, 5).until(
                            EC.element_to_be_clickable((by, selector))
                        )
                        el.click()
                    else:
                        raise
                
                time.sleep(0.5)
                
            elif action_type == 'input':
                el = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((by, selector))
                )
                csv_col = config['csv_mapping'].get(selector)
                value = None
                
                if csv_col == '__RECORDED__':
                    value = str(action.get('value', ''))
                elif csv_col and csv_col in row:
                    # Handle blank/NaN values properly
                    raw_value = row[csv_col]
                    if pd.isna(raw_value):
                        value = ''
                    else:
                        value = str(raw_value)
                        # Convert state names to abbreviations
                        field_id = action.get('field_context', {}).get('id', '').lower()
                        if 'state' in field_id or 'state' in csv_col.lower():
                            state_abbr = STATE_MAPPING.get(value.lower().strip())
                            if state_abbr:
                                value = state_abbr
                else:
                    field_context = action.get('field_context', {})
                    llm_value = infer_field_value_with_llm(field_context, csv_row_dict)
                    if llm_value:
                        value = llm_value
                    else:
                        value = str(action.get('value', ''))
                
                if value is not None:
                    el.clear()
                    if value:  # Only send keys if value is not empty
                        el.send_keys(value)
                time.sleep(0.5)
                    
            elif action_type == 'select':
                from selenium.webdriver.support.ui import Select
                el = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((by, selector))
                )
                select_el = Select(el)
                csv_col = config['csv_mapping'].get(selector)
                value = None
                
                available_options = [opt.text for opt in select_el.options if opt.text.strip()]
                
                if csv_col == '__RECORDED__':
                    value = str(action.get('value', ''))
                elif csv_col and csv_col in row:
                    # Handle blank/NaN values properly
                    raw_value = row[csv_col]
                    if pd.isna(raw_value):
                        value = ''
                    else:
                        value = str(raw_value)
                        # Convert state names to abbreviations
                        field_id = action.get('field_context', {}).get('id', '').lower()
                        if 'state' in field_id or 'state' in csv_col.lower():
                            state_abbr = STATE_MAPPING.get(value.lower().strip())
                            if state_abbr:
                                value = state_abbr
                else:
                    field_context = action.get('field_context', {})
                    llm_value = infer_field_value_with_llm(field_context, csv_row_dict, available_options=available_options)
                    if llm_value:
                        value = llm_value
                    else:
                        value = str(action.get('value', ''))
                
                if value is not None and value != '':
                    select_el.select_by_visible_text(value)
                time.sleep(0.5)
                
        except Exception as e:
            if log_callback:
                log_callback(f"Error on step {step_idx + 1}: {e}")
            raise

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
        
        # Execute all steps for every row
        actions_to_execute = config['actions']
        
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
        ttk.Button(frm_buttons, text="Load Workflow", command=self.on_load_workflow).grid(row=0, column=4, padx=5, pady=5)
        ttk.Button(frm_buttons, text="Edit Workflow", command=self.on_edit_workflow).grid(row=0, column=5, padx=5, pady=5)
        ttk.Button(frm_buttons, text="Verify Workflow", command=self.on_verify_workflow).grid(row=0, column=6, padx=5, pady=5)
        ttk.Button(frm_buttons, text="Run Partial", command=self.on_run_partial).grid(row=0, column=7, padx=5, pady=5)
        ttk.Button(frm_buttons, text="Run Workflow", command=self.on_run_workflow_browser).grid(row=0, column=8, padx=5, pady=5)
        ttk.Button(frm_buttons, text="View Status", command=self.on_view_status).grid(row=0, column=9, padx=5, pady=5)

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
                            
                            # Update CSV mapping
                            config['csv_mapping'] = self.csv_mapping
                            
                            # CRITICAL: Update actions from memory if we have a fresh recording
                            # This prevents verification-corrupted configs from losing steps
                            config_actions_count = len(config.get('actions', []))
                            memory_actions_count = len(self.actions_log) if self.actions_log else 0
                            
                            if memory_actions_count > 0:
                                # We have actions in memory - use them (they're fresher)
                                self.log(f"Updating config with {memory_actions_count} recorded steps (config had {config_actions_count})")
                                config['actions'] = self.actions_log
                                config['verification_complete'] = False  # Need to re-verify
                            else:
                                self.log(f"Preserving config's {config_actions_count} existing steps (no new recording in memory)")
                            
                            with open(config_file, 'w') as f:
                                json.dump(config, f, indent=2)
                            self.log(f"\n✓ Mapping and workflow saved to {config_file}")
                        except Exception as e:
                            self.log(f"Warning: Could not update config file: {e}")
                    else:
                        # Create new config file
                        self.log("No config file exists yet. Creating new one...")
                        self.on_save_config()
                        # Then update with mapping
                        if os.path.exists(config_file):
                            try:
                                with open(config_file, 'r') as f:
                                    config = json.load(f)
                                config['csv_mapping'] = self.csv_mapping
                                with open(config_file, 'w') as f:
                                    json.dump(config, f, indent=2)
                                self.log(f"\n✓ Mapping saved to new config file")
                            except:
                                pass
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

    def on_load_workflow(self):
        """Load and display current workflow configuration."""
        site_name = self.ent_site_name.get().strip()
        
        if not site_name:
            messagebox.showwarning("Missing Site Name", "Please enter a Site Name.")
            return
        
        config_file = f'configs/{site_name}_workflow.json'
        if not os.path.exists(config_file):
            messagebox.showwarning("No Workflow", f"No workflow found for '{site_name}'.\n\nPlease create a workflow first.")
            return
        
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            
            # Create info dialog
            info_win = tk.Toplevel(self.root)
            info_win.title(f"Workflow Info: {site_name}")
            info_win.geometry("700x600")
            
            ttk.Label(info_win, text=f"📋 Workflow Configuration", font=('Arial', 14, 'bold')).pack(pady=10)
            
            # Info frame
            info_frame = ttk.Frame(info_win, padding=10)
            info_frame.pack(fill='both', expand=True, padx=10, pady=5)
            
            info_text = tk.Text(info_frame, width=80, height=30, font=('Courier', 9))
            info_text.pack(fill='both', expand=True)
            
            # Build info display
            info_text.insert('end', f"Site Name: {config.get('site_name', 'N/A')}\n", 'bold')
            info_text.insert('end', f"URL: {config.get('url', 'N/A')}\n\n", 'bold')
            
            info_text.insert('end', f"Config File: {config_file}\n", 'gray')
            info_text.insert('end', f"Last Modified: {time.ctime(os.path.getmtime(config_file))}\n\n", 'gray')
            
            # Verification status
            verified = config.get('verification_complete', False)
            status_text = "✓ VERIFIED" if verified else "⚠ NOT VERIFIED"
            status_color = 'green' if verified else 'orange'
            info_text.insert('end', f"Status: {status_text}\n\n", status_color)
            
            # Steps count
            actions = config.get('actions', [])
            info_text.insert('end', f"Total Steps: {len(actions)}\n", 'bold')
            
            # Deleted steps
            deleted = config.get('deleted_steps', [])
            if deleted:
                info_text.insert('end', f"Deleted Steps (excluded): {len(deleted)}\n", 'orange')
            
            info_text.insert('end', "\n" + "="*60 + "\n\n")
            
            # CSV Mappings
            csv_mapping = config.get('csv_mapping', {})
            if csv_mapping:
                info_text.insert('end', "CSV COLUMN MAPPINGS:\n", 'bold')
                info_text.insert('end', "-" * 60 + "\n")
                for step_idx, col_name in csv_mapping.items():
                    step_name = "Unknown"
                    if int(step_idx) < len(actions):
                        step_name = actions[int(step_idx)].get('step_name', f"Step {int(step_idx)+1}")
                    info_text.insert('end', f"  [{step_idx}] {step_name}\n")
                    info_text.insert('end', f"      → CSV Column: {col_name}\n", 'blue')
                info_text.insert('end', "\n")
            else:
                info_text.insert('end', "No CSV mappings configured.\n\n", 'gray')
            
            # Action summary
            info_text.insert('end', "WORKFLOW STEPS:\n", 'bold')
            info_text.insert('end', "-" * 60 + "\n")
            for i, action in enumerate(actions[:20], 1):  # Show first 20
                action_type = action.get('action', 'unknown').upper()
                step_name = action.get('step_name', f'Step {i}')
                info_text.insert('end', f"  {i}. [{action_type}] {step_name}\n")
            
            if len(actions) > 20:
                info_text.insert('end', f"\n  ... and {len(actions)-20} more steps\n", 'gray')
            
            # Configure tags
            info_text.tag_config('bold', font=('Courier', 9, 'bold'))
            info_text.tag_config('gray', foreground='gray')
            info_text.tag_config('green', foreground='green', font=('Courier', 9, 'bold'))
            info_text.tag_config('orange', foreground='orange', font=('Courier', 9, 'bold'))
            info_text.tag_config('blue', foreground='blue')
            info_text.config(state='disabled')
            
            # Close button
            ttk.Button(info_win, text="Close", command=info_win.destroy).pack(pady=10)
            
            info_win.transient(self.root)
            self.log(f"Loaded workflow info for '{site_name}' - {len(actions)} steps, {len(csv_mapping)} mappings")
            
        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to load workflow: {e}")
            self.log(f"Error loading workflow: {e}")
    
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
            
            # Diagnostic: Show what we're loading
            actions_count = len(config.get('actions', []))
            self.log(f"Loading workflow config: {actions_count} steps found in {config_file}")
            
            # Check if memory has more steps than config
            memory_count = len(self.actions_log) if self.actions_log else 0
            if memory_count > actions_count:
                self.log(f"⚠ WARNING: Memory has {memory_count} steps but config only has {actions_count}!")
                self.log(f"💡 Click 'Map CSV' again to update the config with your latest recording.")
                response = messagebox.askyesno(
                    "Outdated Config Detected",
                    f"Config file has {actions_count} steps\n"
                    f"Memory has {memory_count} steps from your recording\n\n"
                    f"Update config with your latest recording before verifying?"
                )
                if response:
                    config['actions'] = self.actions_log
                    config['verification_complete'] = False
                    with open(config_file, 'w') as f:
                        json.dump(config, f, indent=2)
                    self.log(f"✓ Updated config with {memory_count} steps from memory")
                    actions_count = memory_count
            
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

    def on_run_partial(self):
        """Run workflow for a specific number of rows."""
        site_name = self.ent_site_name.get().strip()
        csv_file = self.ent_csv.get().strip()
        
        if not site_name or not csv_file:
            messagebox.showwarning("Missing Info", "Please provide site name and CSV file.")
            return
        
        if not os.path.exists(csv_file):
            messagebox.showerror("File Not Found", f"CSV file not found: {csv_file}")
            return
        
        # Load CSV to count rows
        try:
            df = pd.read_csv(csv_file)
            total_rows = len(df)
        except Exception as e:
            messagebox.showerror("CSV Error", f"Failed to read CSV: {e}")
            return
        
        # Load processing status
        status = load_processing_status(site_name)
        completed_count = len([k for k, v in status.items() if v.get('status') == 'completed'])
        
        # Show dialog to select number of rows
        partial_win = tk.Toplevel(self.root)
        partial_win.title("Run Partial Workflow")
        partial_win.geometry("450x250")
        
        # Status info
        info_frame = ttk.Frame(partial_win, padding=10)
        info_frame.pack(fill='x')
        ttk.Label(info_frame, text=f"Total Rows: {total_rows}", font=('Arial', 10)).pack(anchor='w')
        ttk.Label(info_frame, text=f"Already Completed: {completed_count}", font=('Arial', 10), foreground='green').pack(anchor='w')
        ttk.Label(info_frame, text=f"Remaining: {total_rows - completed_count}", font=('Arial', 10), foreground='blue').pack(anchor='w')
        
        # Row count selection
        select_frame = ttk.Frame(partial_win, padding=10)
        select_frame.pack(fill='x', padx=10, pady=10)
        
        ttk.Label(select_frame, text="Number of rows to process:").grid(row=0, column=0, sticky='w', pady=5)
        row_count_var = tk.StringVar(value='5')
        row_spinbox = ttk.Spinbox(select_frame, from_=1, to=total_rows, textvariable=row_count_var, width=10)
        row_spinbox.grid(row=0, column=1, pady=5, padx=5)
        
        # Headless mode option
        headless_var = tk.BooleanVar(value=False)
        headless_check = ttk.Checkbutton(
            partial_win, 
            text="⚡ Run browser invisibly (faster, with detailed logs)",
            variable=headless_var
        )
        headless_check.pack(pady=10)
        
        # Quick select buttons
        quick_frame = ttk.Frame(select_frame)
        quick_frame.grid(row=1, column=0, columnspan=2, pady=5)
        
        ttk.Button(quick_frame, text="3", command=lambda: row_count_var.set('3'), width=5).pack(side='left', padx=2)
        ttk.Button(quick_frame, text="5", command=lambda: row_count_var.set('5'), width=5).pack(side='left', padx=2)
        ttk.Button(quick_frame, text="7", command=lambda: row_count_var.set('7'), width=5).pack(side='left', padx=2)
        ttk.Button(quick_frame, text="10", command=lambda: row_count_var.set('10'), width=5).pack(side='left', padx=2)
        
        ttk.Label(partial_win, text="This will process unprocessed rows only.\nAlready completed rows will be skipped.", foreground='gray').pack(pady=10)
        
        def start_partial():
            try:
                count = int(row_count_var.get())
                headless = headless_var.get()
                partial_win.destroy()
                self.run_partial_workflow(count, headless=headless)
            except ValueError:
                messagebox.showerror("Invalid Input", "Please enter a valid number.")
        
        btn_frame = ttk.Frame(partial_win)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Start Processing", command=start_partial).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=partial_win.destroy).pack(side='left', padx=5)
        
        partial_win.transient(self.root)
        partial_win.grab_set()
    
    def run_partial_workflow(self, row_count, headless=False):
        """Run workflow for specified number of unprocessed rows."""
        if headless:
            self.log("Running in headless mode (browser invisible)...")
        site_name = self.ent_site_name.get().strip()
        csv_file = self.ent_csv.get().strip()
        
        self.log(f"Starting partial workflow processing ({row_count} rows)...")
        
        # Load workflow config
        config_path = os.path.join('configs', f'{site_name}_workflow.json')
        
        if not os.path.exists(config_path):
            messagebox.showerror("Config Not Found", f"No workflow config found: {config_path}")
            return
        
        self.log("Loading workflow configuration...")
        
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Filter out deleted steps if present in config
            if 'deleted_steps' in config:
                deleted_steps = config.get('deleted_steps', [])
                self.log(f"Found {len(deleted_steps)} deleted steps in config - these will be excluded")
                # Deleted steps are already excluded from 'actions' array during verification
            
            # Validate actions - remove any with missing selectors (except allowed types)
            original_count = len(config.get('actions', []))
            valid_actions = []
            for action in config.get('actions', []):
                action_type = action.get('action')
                selector = action.get('selector')
                
                # These action types don't need selectors
                if action_type in ('navigate', 'keyboard', 'interactive_sequence'):
                    valid_actions.append(action)
                # These need selectors
                elif selector and selector.strip():
                    valid_actions.append(action)
                else:
                    step_name = action.get('step_name', 'unnamed')
                    self.log(f"⚠ Skipping invalid step '{step_name}' - missing selector")
            
            config['actions'] = valid_actions
            
            if len(valid_actions) < original_count:
                self.log(f"Filtered {original_count - len(valid_actions)} invalid steps. Running with {len(valid_actions)} valid steps.")
            
        except Exception as e:
            messagebox.showerror("Config Error", f"Failed to load config: {e}")
            return
        
        # Load CSV
        try:
            df = pd.read_csv(csv_file)
        except Exception as e:
            messagebox.showerror("CSV Error", f"Failed to read CSV: {e}")
            return
        
        # Load processing status
        status = load_processing_status(site_name)
        
        # Find unprocessed rows
        unprocessed_indices = []
        for idx in range(len(df)):
            if str(idx) not in status or status[str(idx)].get('status') != 'completed':
                unprocessed_indices.append(idx)
        
        if not unprocessed_indices:
            messagebox.showinfo("All Done", "All rows have already been processed!")
            return
        
        # Limit to requested count
        rows_to_process = unprocessed_indices[:row_count]
        
        self.log(f"Found {len(unprocessed_indices)} unprocessed rows. Processing first {len(rows_to_process)}...")
        
        # Run workflow with status tracking
        threading.Thread(target=self._run_partial_thread, args=(config, df, rows_to_process, site_name, headless), daemon=True).start()
    
    def _run_partial_thread(self, config, df, row_indices, site_name, headless=False):
        """Thread to run partial workflow with status tracking."""
        # Create progress window
        progress_win = tk.Toplevel(self.root)
        progress_win.title("Workflow Progress")
        progress_win.geometry("600x400")
        
        ttk.Label(progress_win, text="Workflow Execution Progress", font=('Arial', 12, 'bold')).pack(pady=10)
        
        # Progress info
        info_frame = ttk.Frame(progress_win, padding=10)
        info_frame.pack(fill='x', padx=10)
        
        current_row_label = ttk.Label(info_frame, text="Current Row: -", font=('Arial', 10, 'bold'))
        current_row_label.pack(anchor='w')
        
        current_step_label = ttk.Label(info_frame, text="Current Step: -", font=('Arial', 10))
        current_step_label.pack(anchor='w')
        
        progress_label = ttk.Label(info_frame, text="Progress: 0/0 rows", font=('Arial', 10))
        progress_label.pack(anchor='w')
        
        remaining_label = ttk.Label(info_frame, text="Remaining: 0 rows", font=('Arial', 10), foreground='blue')
        remaining_label.pack(anchor='w')
        
        # Progress bar
        progress_bar = ttk.Progressbar(progress_win, mode='determinate', length=500)
        progress_bar.pack(pady=10, padx=10)
        
        # Status log
        log_frame = ttk.Frame(progress_win)
        log_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        status_text = tk.Text(log_frame, height=15, width=70)
        status_text.pack(fill='both', expand=True)
        status_scrollbar = ttk.Scrollbar(log_frame, command=status_text.yview)
        status_scrollbar.pack(side='right', fill='y')
        status_text.config(yscrollcommand=status_scrollbar.set)
        
        def update_progress(row_num, step_info, completed, total):
            current_row_label.config(text=f"Current Row: {row_num}")
            current_step_label.config(text=f"Current Step: {step_info}")
            progress_label.config(text=f"Progress: {completed}/{total} rows")
            remaining_label.config(text=f"Remaining: {total - completed} rows")
            progress_bar['value'] = (completed / total) * 100
            progress_bar['maximum'] = 100
        
        def log_status(msg, color='black'):
            status_text.insert('end', msg + '\n', color)
            status_text.tag_config(color, foreground=color)
            status_text.see('end')
        
        try:
            driver = init_driver(headless=headless, parent=self.root)
            status = load_processing_status(site_name)
            
            # Navigate to initial URL
            initial_url = config.get('url')
            if initial_url:
                progress_win.after(0, lambda: log_status(f"Navigating to: {initial_url}", 'blue'))
                self.log(f"Navigating to: {initial_url}")
                driver.get(initial_url)
                time.sleep(2)
            
            total_rows = len(row_indices)
            
            for i, row_idx in enumerate(row_indices, 1):
                row = df.iloc[row_idx]
                
                # DIAGNOSTIC: Show what row we're actually processing
                row_data_preview = {k: str(v)[:30] for k, v in list(row.to_dict().items())[:3]}
                
                # Update progress
                progress_win.after(0, lambda r=row_idx: update_progress(r + 1, "Starting...", i - 1, total_rows))
                progress_win.after(0, lambda r=row_idx, d=row_data_preview: log_status(f"\n=== Processing Row {r + 1} (Index: {r}) ===", 'blue'))
                progress_win.after(0, lambda d=row_data_preview: log_status(f"    Data: {d}", 'gray'))
                self.log(f"\n=== Processing Row {row_idx + 1} (CSV Index: {row_idx}) ===")
                self.log(f"    Row data preview: {row_data_preview}")
                
                try:
                    # Custom callback to update step info
                    def step_callback(step_msg):
                        progress_win.after(0, lambda r=row_idx, s=step_msg: update_progress(r + 1, s, i - 1, total_rows))
                        progress_win.after(0, lambda m=step_msg: log_status(f"  {m}", 'black'))
                        self.log(step_msg)
                    
                    # Pass session iteration (i=1 for first row, i=2 for second, etc)
                    replay_workflow_single_row(driver, config, row, log_callback=step_callback, row_idx=row_idx, session_iteration=i)
                    
                    # Mark as completed
                    status[str(row_idx)] = {
                        'status': 'completed',
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'row_number': row_idx + 1
                    }
                    save_processing_status(site_name, status)
                    
                    progress_win.after(0, lambda r=row_idx: log_status(f"✓ Row {r + 1} completed successfully", 'green'))
                    self.log(f"✓ Row {row_idx + 1} completed successfully")
                    
                except Exception as e:
                    # Extract meaningful error message
                    error_msg = str(e).split('\n')[0] if str(e) else "Unknown error"
                    if not error_msg or error_msg == "Message: ":
                        error_msg = "Element not found or browser error - check selectors in Verify Workflow"
                    
                    progress_win.after(0, lambda r=row_idx, err=error_msg: log_status(f"✗ Row {r + 1} failed: {err}", 'red'))
                    progress_win.after(0, lambda: log_status(f"\n❌ STOPPING - First row failed. Fix workflow and try again.", 'red'))
                    self.log(f"✗ Row {row_idx + 1} failed: {error_msg}")
                    self.log(f"\n❌ STOPPING - Workflow has errors. Please verify workflow again.")
                    
                    status[str(row_idx)] = {
                        'status': 'failed',
                        'error': error_msg,
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'row_number': row_idx + 1
                    }
                    save_processing_status(site_name, status)
                    
                    # STOP on first failure
                    driver.quit()
                    return
            
            # Final update
            progress_win.after(0, lambda: update_progress("-", "Complete!", total_rows, total_rows))
            progress_win.after(0, lambda: log_status(f"\n=== Processing Complete ===", 'green'))
            progress_win.after(0, lambda: log_status(f"Processed {total_rows} rows", 'green'))
            
            self.log(f"\n=== Partial Processing Complete ===")
            self.log(f"Processed {len(row_indices)} rows")
            
            driver.quit()
            
        except Exception as e:
            progress_win.after(0, lambda err=e: log_status(f"Error: {err}", 'red'))
            self.log(f"Error in partial workflow: {e}")
    
    def on_view_status(self):
        """View processing status of all rows."""
        site_name = self.ent_site_name.get().strip()
        csv_file = self.ent_csv.get().strip()
        
        if not site_name:
            messagebox.showwarning("Missing Info", "Please provide site name.")
            return
        
        status = load_processing_status(site_name)
        
        if not status:
            messagebox.showinfo("No Status", "No processing status found. Run partial workflow first.")
            return
        
        # Show status dialog
        status_win = tk.Toplevel(self.root)
        status_win.title("Processing Status")
        status_win.geometry("700x500")
        
        ttk.Label(status_win, text="Processing Status", font=('Arial', 12, 'bold')).pack(pady=10)
        
        # Summary
        completed = len([k for k, v in status.items() if v.get('status') == 'completed'])
        failed = len([k for k, v in status.items() if v.get('status') == 'failed'])
        
        summary_frame = ttk.Frame(status_win, padding=10)
        summary_frame.pack(fill='x', padx=10)
        
        ttk.Label(summary_frame, text=f"Completed: {completed}", foreground='green', font=('Arial', 10, 'bold')).pack(side='left', padx=10)
        ttk.Label(summary_frame, text=f"Failed: {failed}", foreground='red', font=('Arial', 10, 'bold')).pack(side='left', padx=10)
        
        # Status list
        list_frame = ttk.Frame(status_win)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        canvas = tk.Canvas(list_frame)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Sort by row number
        sorted_status = sorted(status.items(), key=lambda x: int(x[0]))
        
        for row_idx, info in sorted_status:
            status_val = info.get('status', 'unknown')
            timestamp = info.get('timestamp', 'N/A')
            row_num = info.get('row_number', int(row_idx) + 1)
            
            status_frame = ttk.Frame(scrollable_frame, relief='solid', borderwidth=1, padding=5)
            status_frame.pack(fill='x', padx=5, pady=2)
            
            if status_val == 'completed':
                status_text = f"✓ Row {row_num}: Completed"
                color = 'green'
            elif status_val == 'failed':
                status_text = f"✗ Row {row_num}: Failed - {info.get('error', 'Unknown error')}"
                color = 'red'
            else:
                status_text = f"Row {row_num}: {status_val}"
                color = 'gray'
            
            ttk.Label(status_frame, text=status_text, foreground=color, font=('Arial', 9)).pack(side='left')
            ttk.Label(status_frame, text=f"  |  {timestamp}", foreground='gray', font=('Arial', 8)).pack(side='left')
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Clear status button
        def clear_status():
            response = messagebox.askyesno("Clear Status", "Are you sure you want to clear all processing status? This cannot be undone.")
            if response:
                status_path = os.path.join('configs', f'{site_name}_processing_status.json')
                if os.path.exists(status_path):
                    os.remove(status_path)
                messagebox.showinfo("Cleared", "Processing status has been cleared.")
                status_win.destroy()
        
        btn_frame = ttk.Frame(status_win)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Clear All Status", command=clear_status).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Close", command=status_win.destroy).pack(side='left', padx=5)
        
        status_win.transient(self.root)
    
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
