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
from sendgrid import SendGridAPIClient
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

        if state == "awaiting_user_email_choice":
            user_emails = session["user_emails"]
            matched = next((e for e in user_emails if message in e.lower()), None)

            if not matched:
                email_list = "\n".join([f"- {e}" for e in user_emails])
                return {
                    "status": "awaiting_user_email_choice",
                    "message": f"I didn't recognize that. Please reply with one of the following:\n{email_list}",
                    "options": user_emails
                }

            session["chosen_user_email"] = matched
            session["state"] = "awaiting_confirmation"
            draft = await generate_email_draft(session["recipient"]["name"], session["topic"])
            session["draft"] = draft
            sessions[session_id] = session
            return {
                "status": "awaiting_confirmation",
                "message": draft,
                "recipient": session["recipient"]["name"],
                "recipient_email": session["recipient"]["email"]
            }

        if state == "awaiting_recipient_choice":
            options = session.get("options", [])
            chosen = next((o for o in options if message == o["name"].lower()), None)
            if not chosen:
                chosen = next((o for o in options if message in o["name"].lower()), None)

            if not chosen:
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

        if state == "awaiting_confirmation":
            if any(word in message for word in ["yes", "okay", "go ahead", "send", "sure", "approve"]):
                recipient = session["recipient"]
                message_body = session["draft"]
                subject = f"Follow-up on: {session['topic']}"
                from_email = session.get("chosen_user_email", "default@yourdomain.com")

                email = Mail(
                    from_email=from_email,
                    to_emails=recipient["email"],
                    subject=subject,
                    plain_text_content=message_body
                )

                try:
                    sg = SendGridAPIClient(SENDGRID_API_KEY)
                    sg.send(email)
                except Exception as e:
                    return {"status": "error", "message": str(e)}

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

        extract_prompt = f"""
        Extract the supplier name, email (if any), and topic from this user request. 
        Respond in JSON with keys: \"recipient_name\", \"recipient_email\", and \"topic\".

        User request: \"{data.message}\"
        """

        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Extract supplier name, email (optional), and topic."},
                {"role": "user", "content": extract_prompt}
            ]
        )

        extracted = json.loads(response.choices[0].message.content.strip())
        name = extracted.get("recipient_name", "").strip()
        email = extracted.get("recipient_email", "").strip()
        topic = extracted.get("topic", "").strip()

        if not name:
            return {"status": "error", "message": "Could not extract a name from your request."}

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
                "message": f"I found multiple matches. Please reply with the number or full name of your choice:\n{options_text}",
                "options": options
            }
        else:
            recipient = people[0]

        user_emails_resp = supabase.table("user_emails").select("email").execute()
        user_emails = [e["email"] for e in user_emails_resp.data]

        session = {
            "recipient": recipient,
            "topic": topic
        }

        if len(user_emails) > 1:
            email_list = "\n".join([f"- {e}" for e in user_emails])
            session["state"] = "awaiting_user_email_choice"
            session["user_emails"] = user_emails
            sessions[session_id] = session
            return {
                "status": "awaiting_user_email_choice",
                "message": f"You have multiple emails saved. Please reply with one of the following:\n{email_list}",
                "options": user_emails
            }
        elif len(user_emails) == 1:
            session["chosen_user_email"] = user_emails[0]
        else:
            return {"status": "no_email", "message": "You have no saved emails. Please add one first."}

        draft = await generate_email_draft(recipient["name"], topic)
        session.update({
            "state": "awaiting_confirmation",
            "draft": draft
        })
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
    Keep it professional but friendly. Do not include a sign-off, sender name, or closing line like \"Best regards\" or \"Thanks\".
    Only return the body of the email.
    """
    result = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You write emails that are clear and concise. No name, no sign-off, just the message."},
            {"role": "user", "content": prompt}
        ]
    )
    return result.choices[0].message.content.strip()
