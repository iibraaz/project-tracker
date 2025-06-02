from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from uuid import uuid4
import os
import requests
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI
import traceback
import json

# Load environment variables
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize Supabase and OpenAI clients
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# FastAPI setup
app = FastAPI(title="AI Project Assistant")
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

@app.post("/chat-command")
async def chat_command(data: CommandInput):
    try:
        session_id = data.session_id
        message = data.message

        # Step 1: Extract intent
        extraction_prompt = f"""
You are an AI assistant. Extract structured intent from the user message below.
Respond only in JSON using this format:
{{
  "action": "send_email",
  "recipient_name": "<name of recipient>",
  "topic": "<what the email is about>"
}}

User message: {message}
        """

        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You extract structured intent from user commands."},
                {"role": "user", "content": extraction_prompt}
            ]
        )

        try:
            extracted = json.loads(response.choices[0].message.content.strip())
        except Exception:
            return {"status": "error", "message": "Could not extract structured info from message."}

        recipient_name = extracted.get("recipient_name", "").strip()
        topic = extracted.get("topic", "").strip()

        if not recipient_name:
            return {"status": "need_input", "message": "Who should I send the email to?"}

        # Step 2: Search suppliers
        people_resp = supabase.table("suppliers").select("*").ilike("name", f"%{recipient_name}%").execute()
        matches = people_resp.data

        if not matches:
            return {"status": "not_found", "message": f"No supplier found matching '{recipient_name}'."}

        if len(matches) > 1:
            options = [{"id": p["id"], "name": p["name"], "email": p["email"], "material": p["material"]} for p in matches]
            return {
                "status": "ambiguous",
                "message": f"Multiple suppliers match '{recipient_name}'. Please specify:",
                "options": options
            }

        # Step 3: Single match – generate email draft
        recipient = matches[0]
        email_prompt = f"""
Draft a professional but natural email to a supplier named {recipient['name']} about the following topic: "{topic}". 
Do not use a fixed pattern. Be helpful, clear, and direct. Only include the supplier's name in the greeting.
        """

        email_response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You write emails for professionals."},
                {"role": "user", "content": email_prompt}
            ]
        )

        draft = email_response.choices[0].message.content.strip()

        return {
            "status": "awaiting_confirmation",
            "recipient": recipient["name"],
            "recipient_email": recipient["email"],
            "message": draft
        }

    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": f"Something went wrong: {str(e)}"}
