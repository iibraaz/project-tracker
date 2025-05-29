from pydantic import BaseModel
from typing import List, Optional

class Task(BaseModel):
    id: Optional[str] = None
    title: str
    description: Optional[str] = None
    status: str = "pending"

class Update(BaseModel):
    date: str
    message: str
    engineer: Optional[str] = None

class Project(BaseModel):
    id: Optional[str] = None
    name: str
    description: str
    phases: Optional[List[str]] = []
    tasks: List[Task] = []
    updates: List[Update] = []
