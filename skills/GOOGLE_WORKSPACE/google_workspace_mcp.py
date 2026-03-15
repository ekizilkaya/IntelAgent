import os
import json
import datetime
import csv
from mcp.server.fastmcp import FastMCP
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from dotenv import load_dotenv

load_dotenv()

# Consolidated Scopes for all Google Workspace Integrations
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

mcp = FastMCP("GOOGLE_WORKSPACE")

def get_credentials():
    creds = None
    token_path = os.path.join(os.path.dirname(__file__), 'token.json')
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_id = os.getenv("GOOGLE_CLIENT_ID")
            client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
            if not client_id or not client_secret:
                raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in .env")
            client_config = {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
    return creds

# --- GOOGLE CALENDAR TOOLS ---

@mcp.tool()
def list_events(max_results: int = 10) -> str:
    """List upcoming events from the primary calendar."""
    creds = get_credentials()
    service = build('calendar', 'v3', credentials=creds)
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(calendarId='primary', timeMin=now,
                                          maxResults=max_results, singleEvents=True,
                                          orderBy='startTime').execute()
    events = events_result.get('items', [])
    if not events:
        return "No upcoming events found."
    res = []
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        res.append(f"{start} - {event['summary']}")
    return "\\n".join(res)

@mcp.tool()
def create_event(summary: str, start_time: str, end_time: str, description: str = "") -> str:
    """Create a new event on the primary calendar."""
    creds = get_credentials()
    service = build('calendar', 'v3', credentials=creds)
    event_body = {
        'summary': summary,
        'description': description,
        'start': {'dateTime': start_time},
        'end': {'dateTime': end_time},
    }
    event = service.events().insert(calendarId='primary', body=event_body).execute()
    return f"Event created: {event.get('htmlLink')}"

# --- GOOGLE DRIVE TOOLS ---

@mcp.tool()
def list_drive_files(query: str = "", max_results: int = 10) -> str:
    """List or search files in Google Drive."""
    creds = get_credentials()
    service = build('drive', 'v3', credentials=creds)
    q_param = query if query else None
    results = service.files().list(q=q_param, pageSize=max_results, fields="nextPageToken, files(id, name)").execute()
    items = results.get('files', [])
    if not items:
        return "No files found."
    return json.dumps(items)

@mcp.tool()
def create_drive_folder(name: str) -> str:
    """Create a new folder in Google Drive."""
    creds = get_credentials()
    service = build('drive', 'v3', credentials=creds)
    file_metadata = {
        'name': name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    file = service.files().create(body=file_metadata, fields='id').execute()
    return f"Folder created successfully with ID: {file.get('id')}"

# --- GOOGLE DOCS TOOLS ---

@mcp.tool()
def create_document(title: str) -> str:
    """Create a new Google Document."""
    creds = get_credentials()
    service = build('docs', 'v1', credentials=creds)
    doc = service.documents().create(body={'title': title}).execute()
    return f"Created document with ID: {doc.get('documentId')}"

@mcp.tool()
def read_document(document_id: str) -> str:
    """Read the text content of a Google Document."""
    creds = get_credentials()
    service = build('docs', 'v1', credentials=creds)
    try:
        doc = service.documents().get(documentId=document_id).execute()
        content = doc.get('body').get('content')
        text = ""
        for item in content:
            if 'paragraph' in item:
                elements = item.get('paragraph').get('elements')
                if elements:
                    for elem in elements:
                        if 'textRun' in elem:
                            text += elem.get('textRun').get('content')
        return text
    except Exception as e:
        return f"Error reading document: {e}"

@mcp.tool()
def append_text_to_document(document_id: str, text: str) -> str:
    """Append text to the end of a Google Document."""
    creds = get_credentials()
    service = build('docs', 'v1', credentials=creds)
    try:
        doc = service.documents().get(documentId=document_id).execute()
        if not text.endswith('\\n'):
            text += '\\n'
        requests = [{'insertText': {'location': {'index': 1}, 'text': text}}]
        content = doc.get('body').get('content')
        if content:
            last_idx = content[-1].get('endIndex', 2) - 1
            if last_idx > 0:
                requests[0]['insertText']['location']['index'] = last_idx
        result = service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()
        return f"Successfully appended text to document ID: {document_id}"
    except Exception as e:
        return f"Error appending text: {e}"

@mcp.tool()
def search_document_id_by_title(title: str) -> str:
    """Search Google Drive for a Document by its title to get its document_id."""
    creds = get_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    query = f"mimeType='application/vnd.google-apps.document' and name contains '{title}'"
    results = drive_service.files().list(q=query, pageSize=5, fields="files(id, name)").execute()
    items = results.get('files', [])
    if not items:
        return f"No Google Document found with title containing '{title}'."
    return json.dumps(items)

# --- GOOGLE SHEETS TOOLS ---

@mcp.tool()
def read_sheet(spreadsheet_id: str, range_name: str) -> str:
    """Read values from a Google Sheet."""
    creds = get_credentials()
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
    values = result.get('values', [])
    return json.dumps(values)

@mcp.tool()
def append_sheet(spreadsheet_id: str, range_name: str, values: list) -> str:
    """Append rows to a Google Sheet."""
    creds = get_credentials()
    service = build('sheets', 'v4', credentials=creds)
    body = {'values': values}
    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id, range=range_name,
        valueInputOption='USER_ENTERED', body=body).execute()
    return f"Appended {result.get('updates').get('updatedCells')} cells."

@mcp.tool()
def write_sheet(spreadsheet_id: str, range_name: str, values: list) -> str:
    """Write (overwrite) values to a Google Sheet."""
    creds = get_credentials()
    service = build('sheets', 'v4', credentials=creds)
    body = {'values': values}
    result = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=range_name,
        valueInputOption='USER_ENTERED', body=body).execute()
    return f"Updated {result.get('updatedCells')} cells."

@mcp.tool()
def create_spreadsheet(title: str) -> str:
    """Create a new Google Spreadsheet."""
    creds = get_credentials()
    service = build('sheets', 'v4', credentials=creds)
    spreadsheet = {'properties': {'title': title}}
    spreadsheet = service.spreadsheets().create(body=spreadsheet, fields='spreadsheetId').execute()
    return f"Created spreadsheet with ID: {spreadsheet.get('spreadsheetId')}"

@mcp.tool()
def import_csv_to_spreadsheet(spreadsheet_id: str, csv_path: str, range_name: str = "A1") -> str:
    """Read a local CSV file and overwrite a Google Sheet with its contents."""
    if not os.path.exists(csv_path):
        return f"Error: File not found at {csv_path}"
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            values = list(reader)
    except Exception as e:
        return f"Error reading CSV: {str(e)}"
    if not values:
        return "Error: CSV is empty."
    return write_sheet(spreadsheet_id, range_name, values)

@mcp.tool()
def add_worksheet(spreadsheet_id: str, title: str) -> str:
    """Add a new worksheet (tab) to an existing Google Spreadsheet."""
    creds = get_credentials()
    service = build('sheets', 'v4', credentials=creds)
    requests = [{'addSheet': {'properties': {'title': title}}}]
    body = {'requests': requests}
    result = service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
    new_sheet_id = result.get('replies')[0].get('addSheet').get('properties').get('sheetId')
    return f"Created new worksheet '{title}' with tab ID: {new_sheet_id}"

if __name__ == "__main__":
    mcp.run()
