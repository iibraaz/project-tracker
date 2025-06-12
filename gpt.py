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
                "content": (
                    "You are an email intent parser. For any request to send, write, or compose an email, return ONLY valid JSON in this format: "
                    "{ 'recipient': 'recipient name or email', 'subject': 'Clean subject line', 'message': 'Email body content only' }"\
                    "\nStrictly enforce:\n"
                    "- The subject must be a clean, concise subject line. Do NOT start with 'Subject:' and do NOT include any body content.\n"
                    "- The message must contain ONLY the body of the email, and must NOT duplicate or repeat the subject line.\n"
                    "- Do NOT include markdown, commentary, explanations, or any text outside the JSON.\n"
                    "- Example output: { 'recipient': 'John Smith', 'subject': 'Pricing Inquiry', 'message': 'Following up on our discussion about material costs.' }"
                )
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

async def generate_email_draft(name: str, topic: str) -> dict:
    """Generate an email draft with a clean subject and message body.
    Returns a dict with 'subject' and 'message' keys."""
    prompt = f"""
    Create a professional email to {name} about: {topic}
    Return ONLY a JSON object with exactly these fields:
    - subject: A short, clean subject line (no 'Subject:' prefix)
    - message: The email body content only
    """
    
    try:
        result = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an email drafting assistant. Return ONLY a valid JSON object with exactly two fields:\n"
                        "1. 'subject': A short, clean subject line for the email header. Do not include 'Subject:' prefix.\n"
                        "2. 'message': The email body content only. Do not repeat the subject.\n"
                        "Example: {\"subject\": \"Project Update Meeting\", \"message\": \"I hope this email finds you well...\"}\n"
                        "Do not include any other text, markdown, or formatting outside the JSON object."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        
        response_text = result.choices[0].message.content.strip()
        parsed = json.loads(response_text)
        
        # Validate the response has required fields
        if not isinstance(parsed, dict) or 'subject' not in parsed or 'message' not in parsed:
            raise ValueError("Invalid response format")
            
        # Clean up the subject if it has 'Subject:' prefix
        subject = parsed['subject']
        if subject.lower().startswith('subject:'):
            subject = subject[8:].strip()
            
        return {
            'subject': subject,
            'message': parsed['message']
        }
        
    except (json.JSONDecodeError, ValueError, KeyError, AttributeError) as e:
        # Fallback to default template if parsing fails
        return {
            'subject': f"Follow-up: {topic}",
            'message': f"Dear {name},\n\nI hope this email finds you well. I wanted to follow up regarding {topic}.\n\nBest regards"
        }
