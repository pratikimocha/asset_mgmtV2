"""MSAL SSO integration."""
from flask import current_app
from urllib.parse import urljoin, urlparse
import msal


def build_msal_app(cache=None, authority=None):
    """Build MSAL ConfidentialClientApplication."""
    authority = authority or current_app.config['AZURE_AUTHORITY']
    client_id = current_app.config.get('AZURE_CLIENT_ID')
    client_secret = current_app.config.get('AZURE_CLIENT_SECRET')

    if not client_id or not client_secret:
        return None

    try:
        return msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=authority,
            token_cache=cache
        )
    except Exception:
        return None


def compute_redirect_uri():
    """Compute canonical OAuth redirect URI."""
    from flask import request

    # Check for development override
    redirect_host = current_app.config.get('AZURE_REDIRECT_HOST')
    if redirect_host:
        base_url = redirect_host
    else:
        # Use request host
        base_url = request.host_url.rstrip('/')

    redirect_path = current_app.config.get('AZURE_REDIRECT_PATH', '/auth')
    return urljoin(base_url, redirect_path)


def build_auth_url(authority=None, scopes=None, redirect_uri=None, state=None):
    """Build Microsoft OAuth authorization URL."""
    authority = authority or current_app.config['AZURE_AUTHORITY']
    scopes = scopes or current_app.config.get('AZURE_SCOPE', ['https://graph.microsoft.com/.default'])
    redirect_uri = redirect_uri or compute_redirect_uri()

    msal_app = build_msal_app(authority=authority)
    if msal_app:
        return msal_app.get_authorization_request_url(
            scopes=scopes,
            redirect_uri=redirect_uri,
            state=state or '/'
        )

    # Fallback: manually construct auth URL
    client_id = current_app.config.get('AZURE_CLIENT_ID')
    if not client_id:
        return None

    scope_str = ' '.join(scopes)
    return (
        f'{authority}/oauth2/v2.0/authorize'
        f'?client_id={client_id}'
        f'&response_type=code'
        f'&redirect_uri={redirect_uri}'
        f'&scope={scope_str}'
        f'&state={state or "/"}'
    )
