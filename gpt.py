import openai
import os
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

def generate_project_report(prompt: str) -> str:
    """Send prompt to GPT and return structured project report."""
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {
                "role": "system",
                "content": "You're an expert AI project manager. You analyze project goals, updates, and reports and generate clear structured summaries with phases, tasks, needs, issues, and progress tracking."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.5
    )

    return response['choices'][0]['message']['content']
