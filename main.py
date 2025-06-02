from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from uuid import uuid4
import os
import requests
import json
import traceback
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI

# Load environment variables
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
N8N_WEBHOOK_URL = "https://ibrahimalgazi.app.n8n.cloud/webhook-test/4d888982-1a0e-41e6-a877-f6ebb18460f3"

# Initialize clients
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# FastAPI setup
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CommandInput(BaseModel):
    session_id: str
    message: str

sessions = {}

@app.post("/chat-command")
async def chat_command(data: CommandInput):
    try:
        session_id = data.session_id
        message = data.message.strip().lower()

        session = sessions.get(session_id, {})
        state = session.get("state", "start")

        # STEP 1: Confirming recipient if needed
        if state == "awaiting_recipient_choice":
            options = session.get("options", [])
            # Try exact match first
            chosen = next((o for o in options if message == o["name"].lower()), None)
            # Then try partial match
            if not chosen:
                chosen = next((o for o in options if message in o["name"].lower()), None)
            
            if not chosen:
                # Format names as a numbered list for clear selection
                options_text = "\n".join([f"{i+1}. {o['name']}" for i, o in enumerate(options)])
                return {
                    "status": "ambiguous",
                    "message": f"I found multiple matches. Please reply with the number or full name of your choice:\n{options_text}",
                    "options": options
                }

            session["recipient"] = chosen
            session["state"] = "awaiting_confirmation"
            draft = await generate_email_draft(chosen["name"], session["topic"])
            session["draft"] = draft
            sessions[session_id] = session

            return {
                "status": "awaiting_confirmation",
                "message": draft,
                "recipient": chosen["name"],
                "recipient_email": chosen["email"]
            }

        # STEP 2: User approval or redraft
        if state == "awaiting_confirmation":
            if any(word in message for word in ["yes", "okay", "go ahead", "send", "sure", "approve"]):
                recipient = session["recipient"]
                email_payload = {
                    "to": recipient["email"],
                    "to_name": recipient["name"],
                    "subject": f"Follow-up on: {session['topic']}",
                    "message": session["draft"]
                }
                resp = requests.post(N8N_WEBHOOK_URL, json=email_payload)
                resp.raise_for_status()
                sessions.pop(session_id, None)
                return {"status": "sent", "message": "Email sent successfully."}
            elif any(word in message for word in ["no", "change", "redo", "edit", "revise"]):
                draft = await generate_email_draft(session["recipient"]["name"], session["topic"])
                session["draft"] = draft
                sessions[session_id] = session
                return {
                    "status": "awaiting_confirmation",
                    "message": draft,
                    "recipient": session["recipient"]["name"]
                }
            else:
                return {
                    "status": "awaiting_confirmation",
                    "message": "Should I send this email? Please reply with 'yes' to send or 'no' to revise it."
                }

        # STEP 3: Initial user message
        extract_prompt = f"""
Extract the supplier name and topic from this user request. Respond in JSON with keys \"recipient_name\" and \"topic\".

User request: "{data.message}"
"""
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Extract supplier name and topic."},
                {"role": "user", "content": extract_prompt}
            ]
        )

        try:
            extracted = json.loads(response.choices[0].message.content.strip())
            name = extracted.get("recipient_name", "").strip()
            topic = extracted.get("topic", "").strip()
        except Exception:
            return {"status": "error", "message": "Could not extract recipient name and topic."}

        if not name:
            return {"status": "error", "message": "Could not extract a name from your request."}

        people = supabase.table("suppliers").select("*").ilike("name", f"%{name}%").execute().data

        if not people:
            return {"status": "not_found", "message": f"No suppliers found for '{name}'."}

        elif len(people) > 1:
            options = [
                {
                    "id": supplier["id"],
                    "name": supplier["name"],
                    "email": supplier["email"],
                    "material": supplier.get("material", "")
                }
                for supplier in people
            ]
            
            # Format the options as a clear numbered list
            options_text = "\n".join([f"{i+1}. {supplier['name']}" for i, supplier in enumerate(people)])

            session = {
                "state": "awaiting_recipient_choice",
                "options": options,
                "topic": topic
            }
            sessions[session_id] = session

            return {
                "status": "ambiguous",
                "message": f"I found multiple matches. Please reply with the number or full name of your choice:\n{options_text}",
                "options": options
            }

        else:
            recipient = people[0]
            draft = await generate_email_draft(recipient["name"], topic)
            session = {
                "state": "awaiting_confirmation",
                "recipient": recipient,
                "topic": topic,
                "draft": draft
            }
            sessions[session_id] = session

            return {
                "status": "awaiting_confirmation",
                "message": draft,
                "recipient": recipient["name"],
                "recipient_email": recipient["email"]
            }

    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": f"Something went wrong: {str(e)}"}


async def generate_email_draft(name: str, topic: str) -> str:
    prompt = f"""
Draft a natural, short email to {name} about this topic: \"{topic}\".
Keep it professional but friendly. No fixed patterns. No sign-off needed.
Only return the body of the email.
"""
    result = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You help write professional emails."},
            {"role": "user", "content": prompt}
        ]
    )
    return result.choices[0].message.content.strip()
