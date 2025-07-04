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
import sendgrid
from sendgrid.helpers.mail import Mail

# Load environment variables
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")

# Initialize clients
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)
sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)

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

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/chat-command")
async def chat_command(data: CommandInput):
    try:
        session_id = data.session_id
        message = data.message.strip().lower()
        session = sessions.get(session_id, {})
        state = session.get("state", "start")

        if state == "awaiting_user_email_choice":
            return await handle_email_choice(session_id, message, session)

        if state == "awaiting_recipient_choice":
            return await handle_recipient_choice(session_id, message, session)

        if state == "awaiting_confirmation":
            return await handle_confirmation(session_id, message, session)

        return await handle_new_request(session_id, data.message)

    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": f"Something went wrong: {str(e)}"}

async def handle_email_choice(session_id, message, session):
    user_emails = session["user_emails"]
    matched = next((e for e in user_emails if message in e.lower()), None)

    if not matched:
        email_list = "\n".join([f"- {e}" for e in user_emails])
        return {
            "status": "awaiting_user_email_choice",
            "message": f"Invalid choice. Please reply with one of:\n{email_list}",
            "options": user_emails
        }

    session["chosen_user_email"] = matched

    parsed = await generate_email_draft(session["recipient"]["name"], session["topic"])
    session["draft"] = parsed["message"]
    session["state"] = "awaiting_confirmation"
    sessions[session_id] = session

    return {
        "status": "awaiting_confirmation",
        "message": parsed["message"],
        "recipient": session["recipient"]["name"],
        "recipient_email": session["recipient"]["email"]
    }

async def handle_recipient_choice(session_id, message, session):
    options = session.get("options", [])
    chosen = next((o for o in options if message == o["name"].lower()), None)
    if not chosen:
        chosen = next((o for o in options if message in o["name"].lower()), None)

    if not chosen:
        options_text = "\n".join([f"{i+1}. {o['name']}" for i, o in enumerate(options)])
        return {
            "status": "ambiguous",
            "message": f"I found multiple matches\n{options_text}",
            "options": options
        }

    session["recipient"] = chosen

    user_emails_resp = supabase.table("user_emails").select("email").execute()
    user_emails = [e["email"] for e in user_emails_resp.data]

    if len(user_emails) > 1:
        email_list = "\n".join([f"- {e}" for e in user_emails])
        session["state"] = "awaiting_user_email_choice"
        session["user_emails"] = user_emails
        sessions[session_id] = session
        return {
            "status": "awaiting_user_email_choice",
            "message": f"Multiple sender emails found.\n{email_list}",
            "options": user_emails
        }
    elif len(user_emails) == 1:
        session["chosen_user_email"] = user_emails[0]
    else:
        return {"status": "no_email", "message": "No sender emails saved. Please add one."}

    parsed = await generate_email_draft(chosen["name"], session["topic"])
    session["draft"] = parsed["message"]
    session["state"] = "awaiting_confirmation"
    sessions[session_id] = session

    return {
        "status": "awaiting_confirmation",
        "message": parsed["message"],
        "recipient": chosen["name"],
        "recipient_email": chosen["email"]
    }

async def handle_confirmation(session_id, message, session):
    message = message.lower()

    if any(word in message for word in ["yes", "okay", "go ahead", "send", "sure", "approve"]):
        recipient = session["recipient"]
        from_email = session.get("chosen_user_email")

        if not from_email:
            return {"status": "error", "message": "Missing sender email."}

        parsed = await generate_email_draft(recipient["name"], session["topic"])
        email = Mail(
            from_email=from_email,
            to_emails=recipient["email"],
            subject=parsed["subject"],
            plain_text_content=parsed["message"]
        )

        try:
            sg.send(email)
        except Exception as e:
            return {"status": "error", "message": f"Failed to send email: {str(e)}"}

        sessions.pop(session_id, None)
        return {"status": "sent", "message": "Email sent successfully."}

    elif any(word in message for word in ["no", "change", "redo", "edit", "revise"]):
        parsed = await generate_email_draft(session["recipient"]["name"], session["topic"])
        session["draft"] = parsed["message"]
        sessions[session_id] = session
        return {
            "status": "awaiting_confirmation",
            "message": parsed["message"],
            "recipient": session["recipient"]["name"]
        }

    return {
        "status": "awaiting_confirmation",
        "message": "Should I send this email? Reply with 'yes' or 'no'."
    }

async def handle_new_request(session_id, raw_message):
    extract_prompt = f"""
    Extract the supplier name, email (if any), and topic from this user request. 
    Respond in JSON with keys: \"recipient_name\", \"recipient_email\", and \"topic\".

    User request: \"{raw_message}\"
    """
    response = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "Extract supplier name, email (optional), and topic."},
            {"role": "user", "content": extract_prompt}
        ]
    )

    try:
        extracted = json.loads(response.choices[0].message.content.strip())
    except Exception:
        return {"status": "error", "message": "Failed to extract supplier info from your message."}

    name = extracted.get("recipient_name", "").strip()
    email = extracted.get("recipient_email", "").strip()
    topic = extracted.get("topic", "").strip()

    if not name:
        return {"status": "error", "message": "No supplier name found in your request."}

    if email:
        people = supabase.table("suppliers").select("*").eq("email", email).execute().data
    else:
        people = supabase.table("suppliers").select("*").ilike("name", f"%{name}%").execute().data

    if not people and email:
        new_supplier = {"name": name, "email": email}
        inserted = supabase.table("suppliers").insert(new_supplier).execute()
        recipient = inserted.data[0]
    elif not people:
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
        options_text = "\n".join([f"{i+1}. {supplier['name']}" for i, supplier in enumerate(people)])
        session = {
            "state": "awaiting_recipient_choice",
            "options": options,
            "topic": topic
        }
        sessions[session_id] = session
        return {
            "status": "ambiguous",
            "message": f"I found multiple matches. Please choose one of the options below:\n{options_text}",
            "options": options
        }
    else:
        recipient = people[0]

    user_emails_resp = supabase.table("user_emails").select("email").execute()
    user_emails = [e["email"] for e in user_emails_resp.data]

    session = {"recipient": recipient, "topic": topic}

    if len(user_emails) > 1:
        session["state"] = "awaiting_user_email_choice"
        session["user_emails"] = user_emails
        sessions[session_id] = session
        return {
            "status": "awaiting_user_email_choice",
            "message": f"Multiple sender emails found. Choose one:\n" + "\n".join([f"- {e}" for e in user_emails]),
            "options": user_emails
        }
    elif len(user_emails) == 1:
        session["chosen_user_email"] = user_emails[0]
    else:
        return {"status": "no_email", "message": "No sender emails saved. Please add one."}

    parsed = await generate_email_draft(recipient["name"], topic)
    session.update({
        "state": "awaiting_confirmation",
        "draft": parsed["message"]
    })
    sessions[session_id] = session

    return {
        "status": "awaiting_confirmation",
        "message": parsed["message"],
        "recipient": recipient["name"],
        "recipient_email": recipient["email"]
    }

async def generate_email_draft(name: str, topic: str) -> dict:
    prompt = f"""
    Draft a short, professional, but friendly email to {name} about this topic: \"{topic}\".
    Include a subject line at the top like this:
    Subject: <your subject here>

    Then add the email message starting with:
    Message: <your message here>

    Do not include any sign-off or sender name.
    """

    result = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You write emails that are clear and concise. No sign-off or sender name."},
            {"role": "user", "content": prompt}
        ]
    )

    response = result.choices[0].message.content.strip()
    lines = response.splitlines()
    subject = ""
    message_lines = []
    in_message = False

    for line in lines:
        if line.lower().startswith("subject:"):
            subject = line[len("subject:"):].strip()
        elif line.lower().startswith("message:"):
            in_message = True
            message_lines.append(line[len("message:"):].strip())
        elif in_message:
            message_lines.append(line.strip())

    message = "\n".join(message_lines)
    return {"subject": subject, "message": message}
