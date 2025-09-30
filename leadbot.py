# lead_automation_tool_full.py

import os
import json
import time
import pandas as pd
import PySimpleGUI as sg
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# -------------------------
# Helper Functions
# -------------------------

actions_log = []

def init_driver(headless=False):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless")
    driver = webdriver.Chrome(executable_path="chromedriver.exe", options=options)
    return driver

def detect_dynamic_fields(driver):
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

def map_csv_to_actions(csv_file, actions, driver=None):
    df = pd.read_csv(csv_file)
    mapping = {}
    # Interactive mapping prompt
    for action in actions:
        if action['action'] == 'input':
            # Ask user to select which CSV column maps to this input
            column_choices = df.columns.tolist()
            layout = [
                [sg.Text(f"Map CSV column to input '{action['selector']}'?")],
                [sg.Listbox(values=column_choices, size=(30,6), key='col')],
                [sg.Button("OK")]
            ]
            win = sg.Window("CSV Mapping", layout)
            event, vals = win.read()
            win.close()
            if vals and 'col' in vals and vals['col']:
                mapping[action['selector']] = vals['col'][0]
            else:
                mapping[action['selector']] = None
    unmapped = [k for k,v in mapping.items() if v is None]
    return mapping, unmapped

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
        for action in config['actions']:
            try:
                if action['action'] == 'click':
                    el = driver.find_element(getattr(By, action['by'].upper()), action['selector'])
                    el.click()
                elif action['action'] == 'input':
                    el = driver.find_element(getattr(By, action['by'].upper()), action['selector'])
                    csv_col = config['csv_mapping'].get(action['selector'])
                    if csv_col:
                        el.clear()
                        el.send_keys(str(row[csv_col]))
                elif action['action'] == 'select':
                    from selenium.webdriver.support.ui import Select
                    el = Select(driver.find_element(getattr(By, action['by'].upper()), action['selector']))
                    csv_col = config['csv_mapping'].get(action['selector'])
                    if csv_col:
                        el.select_by_visible_text(str(row[csv_col]))
                time.sleep(0.5)
            except Exception as e:
                print(f"Error on action {action}: {e}")
    driver.quit()

# -------------------------
# GUI Layout
# -------------------------

sg.theme('DarkBlue3')

layout = [
    [sg.Text("Site Name:"), sg.InputText(key='site_name')],
    [sg.Text("Site URL:"), sg.InputText(key='site_url')],
    [sg.Text("Use credentials?")],
    [sg.Text("Username:"), sg.InputText(key='username')],
    [sg.Text("Password:"), sg.InputText(password_char="*", key='password')],
    [sg.Text("CSV File:"), sg.InputText(key='csv_file'), sg.FileBrowse(file_types=(("CSV Files","*.csv"),))],
    [sg.Button("Detect Fields"), sg.Button("Record Workflow"), sg.Button("Map CSV"), sg.Button("Save Config"), sg.Button("Run Workflow")],
    [sg.Multiline(size=(100,20), key='output')]
]

window = sg.Window("Lead Automation Tool", layout)
fields = {}
csv_mapping = {}

while True:
    event, values = window.read()
    if event == sg.WINDOW_CLOSED:
        break

    site_name = values['site_name']
    site_url = values['site_url']
    csv_file = values['csv_file']
    creds = {'username': values['username'], 'password': values['password']} if values['username'] and values['password'] else None

    if event == "Detect Fields":
        window['output'].update("Detecting dynamic fields...\n")
        try:
            driver = init_driver()
            driver.get(site_url)
            fields = detect_dynamic_fields(driver)
            driver.quit()
            output_text = "Detected Fields:\n" + json.dumps(fields, indent=2)
            window['output'].update(output_text)
        except Exception as e:
            window['output'].update(f"Error detecting fields: {e}")

    if event == "Record Workflow":
        window['output'].update("Please manually perform actions in the opened browser. Close browser when done.\n")
        actions_log = []
        driver = init_driver()
        driver.get(site_url)
        sg.popup("Perform your actions in the browser now. Close browser when finished to record.")
        driver.quit()
        window['output'].update(f"Recorded actions:\n{json.dumps(actions_log, indent=2)}")

    if event == "Map CSV":
        if not actions_log:
            window['output'].update("Please record workflow first.\n")
            continue
        if not csv_file:
            window['output'].update("Please select a CSV file.\n")
            continue
        try:
            csv_mapping, unmapped = map_csv_to_actions(csv_file, actions_log)
            output_text = "CSV Mapping Results:\n" + json.dumps(csv_mapping, indent=2)
            if unmapped:
                output_text += f"\n\nUnmapped Inputs: {unmapped}"
            window['output'].update(output_text)
        except Exception as e:
            window['output'].update(f"Error mapping CSV: {e}")

    if event == "Save Config":
        if not actions_log or not csv_mapping:
            window['output'].update("Please record workflow and map CSV first.\n")
            continue
        try:
            filename = save_config(site_name, site_url, creds, actions_log, csv_mapping)
            window['output'].update(f"Configuration saved to {filename}")
        except Exception as e:
            window['output'].update(f"Error saving config: {e}")

    if event == "Run Workflow":
        if not csv_file:
            window['output'].update("Please select a CSV file.\n")
            continue
        config_file = f'configs/{site_name}_workflow.json'
        if not os.path.exists(config_file):
            window['output'].update("Please save workflow config first.\n")
            continue
        try:
            window['output'].update(f"Running workflow for CSV {csv_file}...\n")
            replay_workflow(config_file, csv_file)
            window['output'].update("Workflow completed successfully.")
        except Exception as e:
            window['output'].update(f"Error running workflow: {e}")

window.close()
