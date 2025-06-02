from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client
from openai import OpenAI
import os
import requests
import traceback
import json

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
N8N_WEBHOOK_URL = "https://ibrahimalgazi.app.n8n.cloud/webhook-test/4d888982-1a0e-41e6-a877-f6ebb18460f3"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI()

class CommandInput(BaseModel):
    session_id: str
    message: str

@app.post("/chat-command")
async def chat_command(data: CommandInput):
    try:
        session_id = data.session_id
        user_message = data.message.strip()
        session_resp = supabase.table("sessions").select("*").eq("id", session_id).execute()
        session = session_resp.data[0] if session_resp.data else {}
        context = session.get("context", {}) or {}
        chat_state = context.get("chat_state", "initial")

        def update_session(new_context):
            supabase.table("sessions").upsert({
                "id": session_id,
                "context": new_context,
                "last_message": user_message
            }).execute()

        if chat_state == "awaiting_recipient_choice":
            options = context.get("options", [])
            chosen_name = next((name for name in options if user_message.lower() in name.lower()), None)

            if not chosen_name:
                return {"status": "ambiguous", "message": f"I didn't catch that. Please choose one:", "options": options}

            recipient_resp = supabase.table("suppliers").select("*").eq("name", chosen_name).execute()
            if not recipient_resp.data:
                return {"status": "error", "message": "Selected supplier not found."}
            recipient = recipient_resp.data[0]

            context.update({
                "chat_state": "awaiting_approval",
                "recipient": recipient
            })
            update_session(context)

            draft_email = generate_email(recipient["name"], context["last_topic"])
            context["last_email_message"] = draft_email
            context["last_email_subject"] = f"Follow-up on: {context['last_topic']}"
            update_session(context)

            return {
                "status": "awaiting_approval",
                "recipient": recipient["name"],
                "recipient_email": recipient["email"],
                "subject": context["last_email_subject"],
                "message": draft_email
            }

        elif chat_state == "awaiting_approval":
            recipient = context.get("recipient")
            if not recipient:
                return {"status": "error", "message": "Recipient missing. Please start over."}

            last_message = context.get("last_email_message")
            last_subject = context.get("last_email_subject")
            user_reply = user_message.lower()

            if any(word in user_reply for word in ["yes", "sure", "send", "ok", "okay", "go ahead"]):
                payload = {
                    "to": recipient["email"],
                    "to_name": recipient["name"],
                    "subject": last_subject,
                    "message": last_message
                }
                try:
                    requests.post(N8N_WEBHOOK_URL, json=payload).raise_for_status()
                    context.clear()
                    update_session(context)
                    return {"status": "sent", "message": "Email sent successfully."}
                except Exception as e:
                    return {"status": "error", "message": f"Failed to send email: {str(e)}"}

            elif any(word in user_reply for word in ["no", "change", "redo", "edit", "again"]):
                new_draft = generate_email(recipient["name"], context["last_topic"])
                context["last_email_message"] = new_draft
                context["last_email_subject"] = f"Follow-up on: {context['last_topic']}"
                update_session(context)
                return {
                    "status": "awaiting_approval",
                    "recipient": recipient["name"],
                    "recipient_email": recipient["email"],
                    "subject": context["last_email_subject"],
                    "message": new_draft
                }

            return {"status": "awaiting_approval", "message": "Reply Yes to send or No to rewrite."}

        else:
            extraction = extract_name_and_topic(user_message)
            name = extraction.get("name")
            topic = extraction.get("topic")

            if not name:
                return {"status": "error", "message": "Couldn't extract supplier name."}

            suppliers = supabase.table("suppliers").select("*").ilike("name", f"%{name}%").execute().data

            if not suppliers:
                return {"status": "error", "message": f"No suppliers found for '{name}'."}
            elif len(suppliers) == 1:
                recipient = suppliers[0]
                context.update({
                    "chat_state": "awaiting_approval",
                    "recipient": recipient,
                    "last_topic": topic
                })
                update_session(context)

                draft = generate_email(recipient["name"], topic)
                context["last_email_message"] = draft
                context["last_email_subject"] = f"Follow-up on: {topic}"
                update_session(context)

                return {
                    "status": "awaiting_approval",
                    "recipient": recipient["name"],
                    "recipient_email": recipient["email"],
                    "subject": context["last_email_subject"],
                    "message": draft
                }
            else:
                options = [s["name"] for s in suppliers]
                context.update({
                    "chat_state": "awaiting_recipient_choice",
                    "options": options,
                    "last_topic": topic
                })
                update_session(context)
                return {"status": "ambiguous", "message": "Which one do you mean?", "options": options}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed: {str(e)}")

def extract_name_and_topic(message: str) -> dict:
    try:
        prompt = f'Extract supplier name and topic from: "{message}" as JSON with keys "name" and "topic".'
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You extract structured info from messages."},
                {"role": "user", "content": prompt}
            ]
        )
        return json.loads(response.choices[0].message.content)
    except:
        return {"name": None, "topic": message}

def generate_email(name: str, topic: str) -> str:
    prompt = f"""Write a short, friendly and professional email to {name} about: "{topic}". No signature or closing line."""
    response = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You write short, helpful emails."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()
