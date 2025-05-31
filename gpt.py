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


def parse_command(prompt: str) -> dict:
    """Parses a natural language command into structured JSON with keys: to, subject, message."""
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {
                "role": "system",
                "content": "You're an assistant that turns commands into JSON with fields: to (recipient name), subject, message. Example: 'Send an email to Omar about iron quotation' should return {\"to\": \"Omar\", \"subject\": \"Iron Quotation\", \"message\": \"...\"}"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2
    )

    return eval(response['choices'][0]['message']['content'])  # or use json.loads if GPT outputs JSON string
