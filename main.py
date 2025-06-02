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
N8N_WEBHOOK_URL = "https://ibrahimalgazi.app.n8n.cloud/webhook-test/4d888982-1a0e-41e6-a877-f6ebb18460f3"

# Models
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
    session_id: str
    message: str

# Routes
@app.get("/")
async def root():
    return {"message": "AI Project Assistant API is running."}

@app.post("/projects")
async def create_project(data: ProjectInput):
    try:
        prompt = f"""You are an expert construction project consultant in Dubai. Break down the project goal into phases, give suggestions, timelines, and warnings.

Project goal: {data.project_goal}"""
        if data.num_phases:
            prompt += f" Limit to {data.num_phases} phases."

        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that structures projects."},
                {"role": "user", "content": prompt}
            ]
        )
        plan = response.choices[0].message.content.strip()
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
        summary = update.update_text
        if update.type == "weekly":
            prompt = f"Analyze this weekly update and return needs, issues, and progress:\n{update.update_text}"
            response = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a smart project analyst."},
                    {"role": "user", "content": prompt}
                ]
            )
            summary = response.choices[0].message.content.strip()

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

        supabase.storage.from_("documents").upload(file_path, file_bytes, {"content-type": file.content_type})
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
async def trigger_command(data: CommandInput):
    try:
        session_id = data.session_id
        message = data.message

        # Retrieve session state
        session_resp = supabase.table("sessions").select("step", "context").eq("id", session_id).execute()
        if not session_resp.data:
            step = "initial"
            context = {}
        else:
            session = session_resp.data[0]
            step = session.get("step", "initial")
            context = session.get("context", {}) or {}

        # Chat with GPT to process the message and context
        prompt = f"Context: {context}\nUser: {message}\nSystem: What is the next step? Respond as if you're in a chat, continuing based on the context."
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a conversational assistant helping manage command workflows."},
                {"role": "user", "content": prompt}
            ]
        )
        reply = response.choices[0].message.content.strip()

        # Update session
        supabase.table("sessions").upsert({
            "id": session_id,
            "step": step,
            "context": context,
            "last_message": message,
            "last_response": reply
        }).execute()

        return {"reply": reply}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Command failed: {str(e)}")
