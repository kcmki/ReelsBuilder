import os
import pickle
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaFileUpload, MediaIoBaseUpload

class GoogleDriveManager:
    SCOPES = ['https://www.googleapis.com/auth/drive']

    def __init__(self, credentials_file='./src/credentials.json', token_file='./src/token.pickle'):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.service = self.authenticate()

    # --------------------------
    # Authentication / OAuth2
    # --------------------------
    def authenticate(self):
        creds = None
        # Load token if it exists
        if os.path.exists(self.token_file):
            with open(self.token_file, 'rb') as token:
                creds = pickle.load(token)

        # If no valid credentials, run OAuth flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, self.SCOPES
                )
                creds = flow.run_local_server(port=2222)
            # Save token for next run
            with open(self.token_file, 'wb') as token:
                pickle.dump(creds, token)

        service = build('drive', 'v3', credentials=creds)
        return service

    # --------------------------
    # CRUD Operations
    # --------------------------
    def list_files(self, page_size=10):
        results = self.service.files().list(
            pageSize=page_size,
            fields="nextPageToken, files(id, name, mimeType)"
        ).execute()
        return results.get('files', [])

    def create_file(self, name, mime_type='application/octet-stream', content=None, local_path=None):
        """
        Create a Google Drive file.
        - content: string content to upload in-memory.
        - local_path: path to a local file to upload.
        Progress will be printed to stdout.
        """
        if local_path:
            if not os.path.exists(local_path):
                raise FileNotFoundError(f"{local_path} not found")
            media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
        else:
            # In-memory content
            stream = io.BytesIO(content.encode() if content else b'')
            media = MediaIoBaseUpload(stream, mimetype=mime_type, resumable=True)

        file_metadata = {'name': name}
        request = self.service.files().create(body=file_metadata, media_body=media, fields='id, name')

        return self._execute_resumable_upload(request)

    def update_file(self, file_id, new_name=None, new_content=None, mime_type='application/octet-stream', local_path=None):
        """
        Update a Google Drive file.
        Can update the file's name and/or its content.
        - new_content: string content to upload
        - local_path: path to local file
        """
        if new_name:
            self.service.files().update(fileId=file_id, body={'name': new_name}).execute()

        if local_path or new_content:
            if local_path:
                if not os.path.exists(local_path):
                    raise FileNotFoundError(f"{local_path} not found")
                media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
            else:
                stream = io.BytesIO(new_content.encode())
                media = MediaIoBaseUpload(stream, mimetype=mime_type, resumable=True)

            request = self.service.files().update(fileId=file_id, media_body=media)
            self._execute_resumable_upload(request)

    def make_file_public(self, file_id):
        """
        Make a Google Drive file publicly accessible (anyone with the link).
        Returns the public URL of the file.
        """
        # Create permission: anyone can read
        permission = {
            'type': 'anyone',
            'role': 'reader'
        }

        self.service.permissions().create(
            fileId=file_id,
            body=permission,
            fields='id'
        ).execute()

        # Get file metadata to build public URL
        file = self.service.files().get(
            fileId=file_id,
            fields='id, webViewLink, webContentLink'
        ).execute()

        # Prefer direct download link if available
        return file.get('webContentLink') or file.get('webViewLink')
    def _execute_resumable_upload(self, request):
        """
        Helper to execute a resumable upload with progress display.
        """
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                print(f"Upload progress: {progress}%", end='\r', flush=True)
        print("Upload complete!            ")
        return response
    
    def delete_file(self, file_id):
        self.service.files().delete(fileId=file_id).execute()
