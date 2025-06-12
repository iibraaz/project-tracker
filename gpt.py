import openai
import os
import json
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
    """Parses a natural language command into structured JSON with keys: recipient, subject, message."""
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {
                "role": "system",
                "content": "You are an email intent parser that converts any request to send, write, or compose an email into strict JSON format. Handle both direct commands ('Send an email to John about pricing') and indirect requests ('Can you email Sarah?', 'Would you mind writing to the supplier?', 'Could you reach out to the contractor?'). Always recognize email intent and extract: recipient name, email subject, and message content. Respond ONLY with valid JSON containing exactly these fields: \"recipient\", \"subject\", \"message\". No additional text, explanations, markdown, or formatting. Example output: {\"recipient\": \"John Smith\", \"subject\": \"Pricing Inquiry\", \"message\": \"Following up on our discussion about material costs.\"}"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.1
    )

    response_text = response['choices'][0]['message']['content'].strip()
    
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        # Fallback: try to extract JSON from response if wrapped in markdown or extra text
        import re
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # If all parsing fails, return error structure
        raise ValueError(f"Failed to parse JSON response from GPT: {response_text}")
    except Exception as e:
        raise ValueError(f"Error processing GPT response: {str(e)}")

