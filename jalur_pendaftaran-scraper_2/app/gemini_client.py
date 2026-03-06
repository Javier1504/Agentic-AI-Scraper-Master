from google import genai

def call_gemini_json(api_key: str, model: str, prompt: str) -> str:
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "temperature": 0.2,
        }
    )
    return resp.text or ""
