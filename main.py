from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client
from openai import OpenAI
import os
import requests
import traceback

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

N8N_WEBHOOK_URL = "https://ibrahimalgazi.app.n8n.cloud/webhook-test/4d888982-1a0e-41e6-a877-f6ebb18460f3"

app = FastAPI()

class CommandInput(BaseModel):
    session_id: str
    message: str

@app.post("/chat-command")
async def chat_command(data: CommandInput):
    try:
        session_id = data.session_id
        user_message = data.message.strip()

        # Fetch session data or create new
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

        # Handle states

        if chat_state == "awaiting_recipient_choice":
            options = context.get("options", [])
            normalized_input = user_message.lower()

            matched_recipient = None
            for option in options:
                if normalized_input == option.lower():
                    matched_recipient = option
                    break
            if not matched_recipient:
                for option in options:
                    if normalized_input in option.lower():
                        matched_recipient = option
                        break
            if not matched_recipient:
                update_session(context)
                return {
                    "status": "ambiguous",
                    "message": f"I didn't catch that. Please choose one of these: {options}",
                    "options": options
                }

            people_resp = supabase.table("suppliers").select("*").eq("name", matched_recipient).execute()
            if not people_resp.data:
                update_session(context)
                return {
                    "status": "error",
                    "message": f"Could not find supplier '{matched_recipient}'. Please try again."
                }
            recipient = people_resp.data[0]

            context["chat_state"] = "awaiting_approval"
            context["recipient"] = recipient
            context["last_topic"] = context.get("last_topic", "your request")
            update_session(context)

            email_prompt = f"""
You are a helpful assistant drafting a short, friendly email. Be concise and natural — no formal closings or long paragraphs. Use the recipient's first name and address the topic clearly.

Recipient: {recipient['name']}
Topic: {context['last_topic']}

Only return the email body — no subject, no signatures.
"""
            response = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You help write natural, professional emails."},
                    {"role": "user", "content": email_prompt}
                ]
            )
            draft_message = response.choices[0].message.content.strip()
            draft_subject = f"Follow-up on: {context['last_topic']}"

            # Save draft for approval
            context["last_email_message"] = draft_message
            context["last_email_subject"] = draft_subject
            update_session(context)

            return {
                "status": "awaiting_approval",
                "recipient": recipient["name"],
                "recipient_email": recipient["email"],
                "subject": draft_subject,
                "message": draft_message
            }

        elif chat_state == "awaiting_approval":
            positive_responses = ["yes", "yeah", "yep", "sure", "correct", "go ahead", "send", "ok", "okay"]
            negative_responses = ["no", "not", "change", "edit", "rewrite", "again", "redo"]

            lower_msg = user_message.lower()
            recipient = context.get("recipient")
            last_message = context.get("last_email_message")
            last_subject = context.get("last_email_subject")

            if not last_message or not last_subject or not recipient:
                update_session(context)
                return {"status": "error", "message": "No draft available. Please start over."}

            if any(word in lower_msg for word in positive_responses):
                # Send email
                email_payload = {
                    "to": recipient["email"],
                    "to_name": recipient["name"],
                    "subject": last_subject,
                    "message": last_message
                }
                try:
                    webhook_resp = requests.post(N8N_WEBHOOK_URL, json=email_payload)
                    webhook_resp.raise_for_status()
                except Exception as e:
                    return {"status": "error", "message": f"Failed to send email: {str(e)}"}

                context.clear()
                update_session(context)
                return {"status": "sent", "message": "Email sent successfully."}

            elif any(word in lower_msg for word in negative_responses):
                # Redraft email
                if not recipient:
                    return {"status": "error", "message": "Recipient missing. Please start over."}

                email_prompt = f"""
You are a helpful assistant drafting a short, friendly email. Rewrite the email differently but keep it concise and natural. Use the recipient's first name and address the topic clearly.

Recipient: {recipient['name']}
Topic: {context.get('last_topic', 'your request')}

Only return the email body — no subject, no signatures.
"""
                response = openai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "You help write natural, professional emails."},
                        {"role": "user", "content": email_prompt}
                    ]
                )
                draft_message = response.choices[0].message.content.strip()
                draft_subject = f"Follow-up on: {context.get('last_topic', 'your request')}"

                context["last_email_message"] = draft_message
                context["last_email_subject"] = draft_subject
                update_session(context)

                return {
                    "status": "awaiting_approval",
                    "recipient": recipient["name"],
                    "recipient_email": recipient["email"],
                    "subject": draft_subject,
                    "message": draft_message
                }

            else:
                # Didn't understand approval or rejection
                return {
                    "status": "awaiting_approval",
                    "message": "Please respond with Yes to send the email or No to rewrite it.",
                    "subject": last_subject,
                    "message_body": last_message
                }

        else:
            # Initial step: parse the user message to find intent & recipient
            # Use GPT to extract name and topic

            extraction_prompt = f"""
Extract the supplier name and topic from this user request. Respond in JSON with keys "name" and "topic".

User request: "{user_message}"
"""
            extraction_resp = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You extract supplier name and topic from a user request."},
                    {"role": "user", "content": extraction_prompt}
                ]
            )
            content = extraction_resp.choices[0].message.content.strip()

            import json
            try:
                extracted = json.loads(content)
                name = extracted.get("name")
                topic = extracted.get("topic")
            except Exception:
                # fallback simple extraction: pick first name in message
                words = user_message.split()
                name = words[0]
                topic = user_message

            if not name:
                return {"status": "error", "message": "Could not extract the supplier name from your message."}

            # Search suppliers by name (case insensitive partial match)
            supplier_resp = supabase.table("suppliers").select("*").ilike("name", f"%{name}%").execute()
            matches = supplier_resp.data

            if not matches:
                return {"status": "error", "message": f"No suppliers found matching '{name}'."}
            elif len(matches) == 1:
                recipient = matches[0]
                context = {
                    "chat_state": "awaiting_approval",
                    "recipient": recipient,
                    "last_topic": topic
                }
                update_session(context)

                # Draft email
                email_prompt = f"""
You are a helpful assistant drafting a short, friendly email. Be concise and natural — no formal closings or long paragraphs. Use the recipient's first name and address the topic clearly.

Recipient: {recipient['name']}
Topic: {topic}

Only return the email body — no subject, no signatures.
"""
                response = openai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "You help write natural, professional emails."},
                        {"role": "user", "content": email_prompt}
                    ]
                )
                draft_message = response.choices[0].message.content.strip()
                draft_subject = f"Follow-up on: {topic}"

                context["last_email_message"] = draft_message
                context["last_email_subject"] = draft_subject
                update_session(context)

                return {
                    "status": "awaiting_approval",
                    "recipient": recipient["name"],
                    "recipient_email": recipient["email"],
                    "subject": draft_subject,
                    "message": draft_message
                }
            else:
                # Multiple matches found - list them and ask user to choose
                names_list = [supplier["name"] for supplier in matches]
                context = {
                    "chat_state": "awaiting_recipient_choice",
                    "options": names_list,
                    "last_topic": topic
                }
                update_session(context)
                return {
                    "status": "ambiguous",
                    "message": "I found multiple possibilities. Please specify which one you mean:",
                    "options": names_list
                }

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed: {str(e)}")
