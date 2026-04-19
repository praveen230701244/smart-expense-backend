from google import genai
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

print("API key loaded:", bool(api_key))

client = genai.Client(api_key=api_key)

print("\n🔍 Fetching available models...\n")

try:
    models = client.models.list()

    available = []
    for m in models:
        print(m.name)
        available.append(m.name)

    print("\n✅ Selecting working model...\n")

    # Pick first Gemini model that supports generation
    selected_model = None
    for m in available:
        if "gemini" in m.lower():
            selected_model = m
            break

    if not selected_model:
        raise Exception("❌ No Gemini model available for this API key")

    print("Using model:", selected_model)

    response = client.models.generate_content(
        model=selected_model,
        contents="Say hello in one line"
    )

    print("\n✅ GEMINI RESPONSE:\n")
    print(response.text)

except Exception as e:
    print("\n❌ ERROR:\n")
    print(e)