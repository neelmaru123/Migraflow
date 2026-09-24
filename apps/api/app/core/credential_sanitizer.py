"""
Credential Sanitizer & Security Boundary Guard.
Ensures database credentials, connection URIs, auth tokens, secret keys,
and unmasked raw row payloads never enter:
- LangGraph state
- LLM prompts
- Plan AST blueprints
- Execution event payloads
- Logs and error messages
- AI diagnosis context
- Frontend HTTP responses
"""

from typing import Any, Dict, List, Optional, Set, Union
import re


class CredentialSanitizer:
    """
    Centralized credential redaction and data masking utility.
    """

    # Sensitive key names to redact in dictionary payloads
    SENSITIVE_KEYS: Set[str] = {
        "password",
        "password_hash",
        "secret",
        "secret_key",
        "token",
        "agent_token",
        "api_key",
        "access_token",
        "refresh_token",
        "private_key",
        "authorization",
        "connection_string",
        "conn_str",
        "uri",
        "dsn",
        "db_url",
        "source_password",
        "target_password",
    }

    # Regex patterns for connection URIs and raw credential parameters
    URI_CREDENTIAL_PATTERN = re.compile(
        r'([a-zA-Z0-9+.-]+://)([^:/\s]+):([^@\s/]+)@',
        re.IGNORECASE,
    )
    PASSWORD_ASSIGNMENT_PATTERN = re.compile(
        r'(?i)\b(password|secret|token|api[_-]?key|pwd|auth_token)\s*[:=]\s*([\'"][^\'"\s]+[\'"]|\S+)',
    )
    BEARER_TOKEN_PATTERN = re.compile(
        r'(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*',
    )
    PRIVATE_KEY_PATTERN = re.compile(
        r'-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----',
        re.MULTILINE,
    )

    @classmethod
    def mask_credentials(cls, text: Optional[str]) -> str:
        """
        Redacts credentials, connection URIs, and bearer tokens from a string.
        """
        if not text:
            return ""

        masked = str(text)

        # 1. Mask connection URIs: postgres://user:secret@host -> postgres://user:***REDACTED***@host
        masked = cls.URI_CREDENTIAL_PATTERN.sub(r'\1\2:***REDACTED***@', masked)

        # 2. Mask password/token assignment tokens
        masked = cls.PASSWORD_ASSIGNMENT_PATTERN.sub(r'\1=***REDACTED***', masked)

        # 3. Mask Bearer tokens
        masked = cls.BEARER_TOKEN_PATTERN.sub('Bearer ***REDACTED***', masked)

        # 4. Mask PEM Private Keys
        masked = cls.PRIVATE_KEY_PATTERN.sub('***REDACTED PRIVATE KEY***', masked)

        return masked

    @classmethod
    def sanitize_structure(cls, data: Any, max_depth: int = 10) -> Any:
        """
        Recursively traverses dictionaries, lists, and primitives, redacting any
        sensitive keys or credential strings.
        """
        if max_depth <= 0:
            return data

        if isinstance(data, dict):
            sanitized_dict = {}
            for k, v in data.items():
                k_lower = str(k).lower()
                if any(s in k_lower for s in cls.SENSITIVE_KEYS):
                    sanitized_dict[k] = "***REDACTED***"
                else:
                    sanitized_dict[k] = cls.sanitize_structure(v, max_depth=max_depth - 1)
            return sanitized_dict

        elif isinstance(data, (list, tuple, set)):
            sanitized_list = [cls.sanitize_structure(item, max_depth=max_depth - 1) for item in data]
            return sanitized_list if not isinstance(data, tuple) else tuple(sanitized_list)

        elif isinstance(data, str):
            return cls.mask_credentials(data)

        return data

    @classmethod
    def sanitize_for_ai_diagnosis(
        cls,
        error_message: str,
        stage: Optional[str] = None,
        table_name: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Prepares a security-hardened diagnostic context for AI error analysis.
        Strictly excludes raw table rows and unmasked database credentials.
        """
        clean_msg = cls.mask_credentials(error_message)
        clean_ctx = cls.sanitize_structure(context or {})

        # Ensure no raw row arrays are leaked in diagnostic context
        for key in list(clean_ctx.keys()):
            if "row" in key.lower() or "data" in key.lower() or "record" in key.lower():
                if isinstance(clean_ctx[key], (list, dict)):
                    clean_ctx[key] = f"<{len(clean_ctx[key])} rows redacted for security>"

        return {
            "stage": stage or "execution",
            "table_name": table_name or "N/A",
            "error_message": clean_msg,
            "context": clean_ctx,
        }
