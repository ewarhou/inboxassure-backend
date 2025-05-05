# call_openrouter.py

from openai import OpenAI

# Your OpenRouter API Key (Hardcoded as requested)
OPENROUTER_API_KEY = "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"

# --- Optional Headers (Change if needed) ---
# Replace with your actual site URL if you want it included for rankings
YOUR_SITE_URL = "https://inboxassure.app" 
# Replace with your actual site name if you want it included for rankings
YOUR_SITE_NAME = "InboxAssure"
# -----------------------------------------

# Initialize the client
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

def call_openrouter(prompt: str, model: str = "meta-llama/llama-4-scout:free") -> str | None:
    """Calls the OpenRouter API with a given prompt and model."""
    completion = client.chat.completions.create(
        extra_headers={
            "HTTP-Referer": YOUR_SITE_URL, 
            "X-Title": YOUR_SITE_NAME, 
        },
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