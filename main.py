import os
import json
import shutil
import logging
from logging.handlers import TimedRotatingFileHandler
import dotenv
from datetime import datetime, timedelta
from notion_client import Client
import glob

# Load environment variables
ENV_FILE = ".env.dev" if os.getenv("ENV", "dev") == "dev" else ".env"
dotenv.load_dotenv(ENV_FILE)

# Logging configuration
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "app.log")

# Configure logging with rotation
logger = logging.getLogger()
logger.setLevel(logging.DEBUG if ENV_FILE == ".env.dev" else logging.INFO)

# Create formatters
formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(filename)s - %(funcName)s - Line: %(lineno)d - %(message)s"
)

# File handler with rotation
file_handler = TimedRotatingFileHandler(
    log_file,
    when="midnight",
    interval=1,
    backupCount=7,
    encoding="utf-8"
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Console handler for dev environment
if ENV_FILE == ".env.dev":
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

# Constants
notion = Client(auth=os.getenv("NOTION_API_KEY"))
TARGET_DIR = "target"
os.makedirs(TARGET_DIR, exist_ok=True)
PROJECTS_JSON_FILE = os.path.join(TARGET_DIR, "notion-projects.json")
REPORT_FILE_MD = os.path.join(TARGET_DIR, "tasks_report.md")
REPORT_FILE_TXT = os.path.join(TARGET_DIR, "tasks_report.txt")
SAMPLE_TASK_FILE = os.path.join(TARGET_DIR, "sample_task.json")

def cleanup_old_files():
    """Clean up files older than 7 days in the target directory."""
    current_time = datetime.now()
    for file_pattern in [f"{TARGET_DIR}/tasks_report_*.md", f"{TARGET_DIR}/tasks_report_*.txt"]:
        for file_path in glob.glob(file_pattern):
            file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            if (current_time - file_time).days > 7:
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted old file: {file_path}")
                except Exception as e:
                    logger.error(f"Error deleting file {file_path}: {str(e)}")

def refresh_projects_info():
    """Fetches project information from Notion and caches it in a JSON file."""
    if os.path.exists(PROJECTS_JSON_FILE):
        last_modified = datetime.fromtimestamp(os.path.getmtime(PROJECTS_JSON_FILE))
        if datetime.now() - last_modified < timedelta(hours=24):
            logger.info("Project info is up to date.")
            return
    
    logger.info("Fetching projects from Notion API...")
    projects_data = notion.databases.query(database_id=os.getenv("NOTION_PROJECTS_DB"))
    
    projects_info = {
        "generated_at": datetime.now().isoformat(),
        "projects": {
            project["id"]: project["properties"]["Name"]["title"][0]["text"]["content"]
            for project in projects_data.get("results", []) 
            if "Name" in project["properties"]
        }
    }
    
    with open(PROJECTS_JSON_FILE, "w") as f:
        json.dump(projects_info, f, indent=4)
    logger.info("Projects info refreshed.")

def get_tasks():
    """Fetches open tasks from Notion and returns them in categorized lists."""
    logger.info("Fetching tasks from Notion API...")
    
    try:
        # First, get tasks that we need for the report using filters
        today = datetime.now().date()
        last_week = today - timedelta(days=7)
        
        # Initialize result lists
        high_priority = []
        due_today = []
        overdue = []
        no_due_date_count = 0
        older_overdue_count = 0
        
        # Filter for high priority tasks that are due on or before today
        high_priority_filter = {
            "and": [
                {
                    "property": "Status",
                    "status": {
                        "does_not_equal": "Done"
                    }
                },
                {
                    "property": "Priority",
                    "status": {
                        "equals": "High"
                    }
                },
                {
                    "property": "Due",
                    "date": {
                        "on_or_before": today.isoformat()
                    }
                }
            ]
        }
        
        # Filter for tasks due today or overdue (excluding future tasks)
        due_today_filter = {
            "and": [
                {
                    "property": "Status",
                    "status": {
                        "does_not_equal": "Done"
                    }
                },
                {
                    "property": "Due",
                    "date": {
                        "equals": today.isoformat()
                    }
                }
            ]
        }
        
        # Filter for overdue tasks in last 7 days (excluding future tasks)
        overdue_filter = {
            "and": [
                {
                    "property": "Status",
                    "status": {
                        "does_not_equal": "Done"
                    }
                },
                {
                    "property": "Due",
                    "date": {
                        "on_or_before": today.isoformat(),
                        "on_or_after": last_week.isoformat()
                    }
                }
            ]
        }
        
        # Filter for tasks with no due date
        no_due_date_filter = {
            "and": [
                {
                    "property": "Status",
                    "status": {
                        "does_not_equal": "Done"
                    }
                },
                {
                    "property": "Due",
                    "date": {
                        "is_empty": True
                    }
                }
            ]
        }
        
        # Filter for tasks overdue for more than 7 days (excluding future tasks)
        older_overdue_filter = {
            "and": [
                {
                    "property": "Status",
                    "status": {
                        "does_not_equal": "Done"
                    }
                },
                {
                    "property": "Due",
                    "date": {
                        "before": last_week.isoformat()
                    }
                }
            ]
        }
        
        def process_task(task):
            """Helper function to process a single task."""
            try:
                properties = task.get("properties", {})
                task_name = properties.get("Name", {}).get("title", [{}])[0].get("text", {}).get("content", "Unknown Task")
                task_url = task.get("url", "")
                
                # Safely handle project relation
                project_id = None
                project_relation = properties.get("Project", {}).get("relation", [])
                if project_relation and len(project_relation) > 0:
                    project_id = project_relation[0].get("id")
                
                # Safely handle status
                status = properties.get("Status", {}).get("status", {}).get("name", "Unknown")
                
                return (task_name, task_url, project_id, status)
            except Exception as e:
                logger.error(f"Error processing task: {str(e)}")
                return None
        
        # Get tasks for each category
        try:
            high_priority_data = notion.databases.query(
                database_id=os.getenv("NOTION_TASKS_DB"),
                filter=high_priority_filter,
                properties=["Name", "Project", "Due", "Status", "Priority"]
            )
            high_priority = [task for task in (process_task(t) for t in high_priority_data.get("results", [])) if task]
            logger.info(f"Found {len(high_priority)} high priority tasks")
        except Exception as e:
            logger.error(f"Error fetching high priority tasks: {str(e)}")
        
        try:
            due_today_data = notion.databases.query(
                database_id=os.getenv("NOTION_TASKS_DB"),
                filter=due_today_filter,
                properties=["Name", "Project", "Due", "Status"]
            )
            due_today = [task for task in (process_task(t) for t in due_today_data.get("results", [])) if task]
            logger.info(f"Found {len(due_today)} due today tasks")
        except Exception as e:
            logger.error(f"Error fetching due today tasks: {str(e)}")
        
        try:
            overdue_data = notion.databases.query(
                database_id=os.getenv("NOTION_TASKS_DB"),
                filter=overdue_filter,
                properties=["Name", "Project", "Due", "Status"]
            )
            overdue = [task for task in (process_task(t) for t in overdue_data.get("results", [])) if task]
            logger.info(f"Found {len(overdue)} overdue tasks")
        except Exception as e:
            logger.error(f"Error fetching overdue tasks: {str(e)}")
        
        # Get counts for other categories
        try:
            no_due_date_data = notion.databases.query(
                database_id=os.getenv("NOTION_TASKS_DB"),
                filter=no_due_date_filter,
                properties=["Name", "Project", "Due", "Status"]
            )
            no_due_date_count = len(no_due_date_data.get("results", []))
            logger.info(f"Found {no_due_date_count} tasks with no due date")
        except Exception as e:
            logger.error(f"Error fetching tasks with no due date: {str(e)}")
        
        try:
            older_overdue_data = notion.databases.query(
                database_id=os.getenv("NOTION_TASKS_DB"),
                filter=older_overdue_filter,
                properties=["Name", "Project", "Due", "Status"]
            )
            older_overdue_count = len(older_overdue_data.get("results", []))
            logger.info(f"Found {older_overdue_count} tasks overdue for more than 7 days")
        except Exception as e:
            logger.error(f"Error fetching older overdue tasks: {str(e)}")
        
        logger.info(f"High priority tasks: {high_priority}")
        logger.info(f"Due today tasks: {due_today}")
        logger.info(f"Overdue tasks: {overdue}")
        logger.info(f"Tasks with no due date: {no_due_date_count}")
        logger.info(f"Tasks overdue for more than 7 days: {older_overdue_count}")
        
        return high_priority, due_today, overdue, no_due_date_count, older_overdue_count
        
    except Exception as e:
        logger.error(f"Unexpected error in get_tasks: {str(e)}")
        return [], [], [], 0, 0

def generate_report():
    """Generates a Markdown report of tasks."""
    try:
        high_priority, due_today, overdue, no_due_date_count, older_overdue_count = get_tasks()
        logger.debug("Now generating report")
        
        # Load projects info
        with open(PROJECTS_JSON_FILE, "r") as f:
            projects_info = json.load(f)
        
        # Archive existing reports if they exist
        if os.path.exists(REPORT_FILE_MD):
            timestamp = datetime.now().strftime("%Y_%m_%d")
            shutil.move(REPORT_FILE_MD, f"{TARGET_DIR}/tasks_report_{timestamp}.md")
            shutil.move(REPORT_FILE_TXT, f"{TARGET_DIR}/tasks_report_{timestamp}.txt")
        
        # Clean up old files
        cleanup_old_files()
        
        with open(REPORT_FILE_MD, "w") as md_file, open(REPORT_FILE_TXT, "w") as txt_file:
            report_title = f"# Task Report ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n\n"
            md_file.write(report_title)
            txt_file.write(report_title)

            def write_section(title, tasks):
                if tasks:
                    try:
                        md_file.write(f"## {title}\n")
                        txt_file.write(f"{title}:\n")
                        for task in tasks:
                            try:
                                task_name, task_url, project_id, status = task
                                project_name = projects_info["projects"].get(project_id, "Unknown Project")
                                
                                # Format the task line
                                task_line = f"- [ ] [{task_name}]({task_url})"
                                if project_name != "Unknown Project":
                                    task_line += f" (Project: {project_name})"
                                if status and status != "Unknown":
                                    task_line += f", Status: {status}"
                                
                                md_file.write(task_line + "\n")
                                txt_file.write(task_line.replace("[", "").replace("]", "") + "\n")
                            except Exception as e:
                                logger.error(f"Error writing task to report: {str(e)}")
                                continue
                        md_file.write("\n")
                        txt_file.write("\n")
                    except Exception as e:
                        logger.error(f"Error writing section {title}: {str(e)}")

            write_section("High Priority", high_priority)
            write_section("Due Today", due_today)
            write_section("Overdue (Last 7 Days)", overdue)
            
            if older_overdue_count:
                md_file.write(f"\n*Note: {older_overdue_count} tasks are overdue for more than 7 days.*\n")
                txt_file.write(f"\nNote: {older_overdue_count} tasks are overdue for more than 7 days.\n")
            
            if no_due_date_count:
                md_file.write(f"\n*Note: {no_due_date_count} tasks have no Due.*\n")
                txt_file.write(f"\nNote: {no_due_date_count} tasks have no Due.\n")
        
        logger.info("Task report generated successfully.")
    except Exception as e:
        logger.error(f"Error generating report: {str(e)}")
        raise

def analyze_sample_task():
    """Fetches and analyzes a sample task to understand its structure."""
    try:
        sample_task_id = os.getenv("SAMPLE_TASK_PAGE_ID")
        if not sample_task_id:
            logger.error("SAMPLE_TASK_PAGE_ID not found in environment variables")
            return
        
        logger.info(f"Fetching sample task with ID: {sample_task_id}")
        task = notion.pages.retrieve(page_id=sample_task_id)
        
        # Save the complete task data
        with open(SAMPLE_TASK_FILE, "w") as f:
            json.dump(task, f, indent=4)
        
        logger.info(f"Sample task data saved to {SAMPLE_TASK_FILE}")
        
        # Print the task data
        logger.info("Sample task data:")
        logger.info(json.dumps(task, indent=2))
        
    except Exception as e:
        logger.error(f"Error analyzing sample task: {str(e)}")

if __name__ == "__main__":
    # analyze_sample_task()
    refresh_projects_info()
    generate_report()