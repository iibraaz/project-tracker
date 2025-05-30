from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from uuid import uuid4
import os
import requests
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI
import traceback

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

# Webhook URL for n8n
N8N_WEBHOOK_URL = "https://ibrahimalgazi.app.n8n.cloud/webhook-test/e73ca2e1-ab3f-4009-b6b3-d9c6a08e851c"

# Pydantic models
class ProjectInput(BaseModel):
    user_id: str
    project_name: str
    project_goal: str
    num_phases: Optional[int] = None

class UpdateInput(BaseModel):
    project_id: str
    update_text: str
    type: str  # "daily" or "weekly"

class CommandInput(BaseModel):
    type: str  # "send_email", "order_supply", etc.
    payload: dict

# Root
@app.get("/")
async def root():
    return {"message": "AI Project Assistant API is running."}

# Project creation
@app.post("/projects")
async def create_project(data: ProjectInput):
    try:
        gpt_prompt = f"""You are an expert construction project consultant in Dubai. Break down the project goal into phases, give suggestions, timelines, and warnings.

Project goal: {data.project_goal}"""
        if data.num_phases:
            gpt_prompt += f" Limit to {data.num_phases} phases."

        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that structures projects."},
                {"role": "user", "content": gpt_prompt}
            ]
        )
        plan = response.choices[0].message.content

        project_id = str(uuid4())
        supabase.table("projects").insert({
            "id": project_id,
            "user_id": data.user_id,
            "name": data.project_name,
            "goal": data.project_goal,
            "plan": plan
        }).execute()

        return {"project_id": project_id, "plan": plan}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create project: {str(e)}")

# Project updates
@app.post("/updates")
async def submit_update(update: UpdateInput):
    try:
        if update.type == "weekly":
            prompt = f"Analyze this weekly update and return needs, issues, and progress:\n{update.update_text}"
            response = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a smart project analyst."},
                    {"role": "user", "content": prompt}
                ]
            )
            summary = response.choices[0].message.content
        else:
            summary = update.update_text

        supabase.table("updates").insert({
            "project_id": update.project_id,
            "type": update.type,
            "original": update.update_text,
            "summary": summary
        }).execute()

        return {"summary": summary}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to submit update: {str(e)}")

# File upload
@app.post("/upload")
async def upload_document(file: UploadFile = File(...), project_id: str = Form(...)):
    try:
        file_bytes = await file.read()
        file_path = f"documents/{project_id}/{file.filename}"

        supabase.storage.from_("documents").upload(file_path, file_bytes)
        public_url = supabase.storage.from_("documents").get_public_url(file_path).get("publicURL")

        supabase.table("documents").insert({
            "project_id": project_id,
            "file_name": file.filename,
            "url": public_url
        }).execute()

        return {"url": public_url}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

# Command trigger (including email)
@app.post("/trigger-command")
async def trigger_command(command: CommandInput):
    try:
        if command.type == "send_email":
            recipient_name = command.payload.get("recipient")
            subject = command.payload.get("subject")
            message = command.payload.get("message")

            if not recipient_name or not subject or not message:
                raise HTTPException(status_code=400, detail="Missing recipient, subject, or message.")

            # Fetch email from Supabase
            supplier_response = supabase.table("suppliers").select("email").ilike("name", f"%{recipient_name}%").execute()
            records = supplier_response.data

            if not records or len(records) == 0:
                raise HTTPException(status_code=404, detail=f"No supplier found with name '{recipient_name}'.")
            if len(records) > 1:
                raise HTTPException(status_code=400, detail=f"Multiple suppliers found with name '{recipient_name}'.")

            recipient_email = records[0]["email"]

            # Send to n8n
            response = requests.post(N8N_WEBHOOK_URL, json={
                "type": "send_email",
                "payload": {
                    "to": recipient_email,
                    "subject": subject,
                    "message": message
                }
            })
            response.raise_for_status()
            return {"status": "sent", "recipient_email": recipient_email, "response": response.json()}

        # Handle other command types
        else:
            response = requests.post(N8N_WEBHOOK_URL, json=command.dict())
            response.raise_for_status()
            return {"status": "sent", "response": response.json()}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Command failed: {str(e)}")
