# call_openrouter.py

from openai import OpenAI
from django.conf import settings # Import Django settings
import os # Import os to allow fallback if settings not configured

# Removed hardcoded key
# OPENROUTER_API_KEY = "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"

# Get key from Django settings (loaded from .env) or fallback to os.environ
OPENROUTER_API_KEY = getattr(settings, 'OPENROUTER_API_KEY', os.environ.get('OPENROUTER_API_KEY'))

# --- Optional Headers (Change if needed) ---
# Replace with your actual site URL if you want it included for rankings
YOUR_SITE_URL = "https://inboxassure.app" 
# Replace with your actual site name if you want it included for rankings
YOUR_SITE_NAME = "InboxAssure"
# -----------------------------------------

# Check if the key was loaded
if not OPENROUTER_API_KEY:
    print("Error: OPENROUTER_API_KEY not found in Django settings or environment variables.")
    # Optionally raise an error or exit, depending on desired behavior
    # raise ValueError("OPENROUTER_API_KEY not configured")
    client = None # Prevent client initialization if key is missing
else:
    # Initialize the client WITH the api_key argument
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

def call_openrouter(prompt: str, model: str = "meta-llama/llama-4-scout:free") -> str | None:
    """Calls the OpenRouter API with a given prompt and model."""
    if not client:
        print("Error: OpenRouter client not initialized due to missing API key.")
        return None
        
    # No explicit Authorization header needed here
    headers_to_send = {
        "HTTP-Referer": YOUR_SITE_URL, 
        "X-Title": YOUR_SITE_NAME, 
    }
    
    # Exceptions will now propagate to the caller (e.g., signal handler)
    completion = client.chat.completions.create(
        extra_headers=headers_to_send, 
        model=model,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )
    return completion.choices[0].message.content

# Example usage:
if __name__ == "__main__":
    example_prompt = "give answer directly, no additional text, what's 1+1?"
    print(f"Sending prompt to OpenRouter: \"{example_prompt}\"")
    response = call_openrouter(example_prompt)
    
    if response:
        print("\nOpenRouter Response:")
        print(response)
    else:
        print("\nFailed to get response from OpenRouter.") 