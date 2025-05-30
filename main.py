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
    type: str  # e.g. "send_email"
    payload: dict

# Endpoints
@app.get("/")
async def root():
    return {"message": "AI Project Assistant API is running."}

@app.post("/projects")
async def create_project(data: ProjectInput):
    try:
        gpt_prompt = f"""You are an expert construction project consultant in Dubai. Break down the following project goal into phases with timelines, expert tips, risks, and warnings:\n\n{data.project_goal}"""
        if data.num_phases:
            gpt_prompt += f"\n\nLimit the breakdown to {data.num_phases} phases."

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

@app.post("/updates")
async def submit_update(update: UpdateInput):
    try:
        if update.type == "weekly":
            prompt = f"Analyze this weekly update and list key needs, issues, and progress:\n{update.update_text}"
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

@app.post("/trigger-command")
async def trigger_command(command: CommandInput):
    try:
        if command.type == "send_email":
            recipient_name = command.payload.get("recipient_name")
            subject = command.payload.get("subject")
            body_context = command.payload.get("body_context")

            # Fetch supplier email from Supabase
            supplier_response = supabase.table("suppliers").select("email").eq("name", recipient_name).single().execute()
            if supplier_response.data is None:
                raise HTTPException(status_code=404, detail=f"Supplier '{recipient_name}' not found.")

            recipient_email = supplier_response.data["email"]

            # Generate email content using GPT
            gpt_response = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You write professional supplier emails."},
                    {"role": "user", "content": f"Write a formal email to {recipient_name} about: {body_context}"}
                ]
            )
            email_body = gpt_response.choices[0].message.content

            # Send to n8n
            n8n_payload = {
                "type": "send_email",
                "recipient_name": recipient_name,
                "recipient_email": recipient_email,
                "subject": subject,
                "body": email_body
            }

            response = requests.post(N8N_WEBHOOK_URL, json=n8n_payload)
            response.raise_for_status()

            return {
                "status": "sent",
                "to": recipient_email,
                "subject": subject,
                "body": email_body
            }

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported command type: {command.type}")

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Command failed: {str(e)}")
