import os
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger("0xItachi.WorkflowManager")

WORKFLOWS_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data",
    "workflows.json"
)

class WorkflowManager:
    """
    Personal Workflow Memory Manager for 0xItachi.
    Stores and retrieves procedural workflows learned from successful browser tasks.
    """
    def __init__(self, file_path: str = WORKFLOWS_FILE_PATH):
        self.file_path = file_path
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        self.workflows: Dict[str, dict] = self._load_workflows()

    def _load_workflows(self) -> Dict[str, dict]:
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading workflows from {self.file_path}: {e}")
                return {}
        return {}

    def _save_workflows(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.workflows, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {len(self.workflows)} workflow(s) to persistent storage.")
        except Exception as e:
            logger.error(f"Error saving workflows: {e}")

    def find_matching_workflow(self, goal: str) -> Optional[dict]:
        """
        Finds a learned workflow matching the given goal keywords.
        """
        import string
        STOP_WORDS = {"go", "to", "the", "and", "for", "read", "what", "they", "are", "about", "with", "from", "get", "find"}
        
        query_words = [
            w.lower().strip(string.punctuation) 
            for w in goal.split() 
            if len(w.strip(string.punctuation)) > 2 and w.lower().strip(string.punctuation) not in STOP_WORDS
        ]
        
        if not query_words:
            return None

        best_match = None
        highest_score = 0.0

        for wf_id, wf in self.workflows.items():
            keywords = set(
                k.lower().strip(string.punctuation) 
                for k in wf.get("keywords", []) 
                if k.lower().strip(string.punctuation) not in STOP_WORDS
            )
            
            match_count = sum(1 for q_word in query_words if q_word in keywords)
            if match_count > 0:
                score = match_count / max(len(query_words), 1)
                logger.debug(f"Match check: '{wf['name']}' -> count={match_count}/{len(query_words)}, score={score:.2f}")
                if score > highest_score:
                    highest_score = score
                    best_match = wf

        if best_match and highest_score >= 0.5:
            logger.info(f"🧠 Persistent Memory Match Found! Workflow: '{best_match['name']}' (Confidence: {best_match['confidence_score']*100:.0f}%)")
            return best_match

        return None

    def save_workflow_from_history(self, goal: str, history: any):
        """
        Extracts action steps from an AgentHistoryList and stores them as a learned workflow.
        """
        if not history or not hasattr(history, "all_model_outputs"):
            return

        actions_taken = []
        for model_output in history.all_model_outputs:
            output_dict = {}
            if hasattr(model_output, "model_dump"):
                output_dict = model_output.model_dump()
            elif isinstance(model_output, dict):
                output_dict = model_output
            
            if output_dict:
                # AgentOutput usually has an action list or direct fields
                if "action" in output_dict and isinstance(output_dict["action"], list):
                    for act in output_dict["action"]:
                        if hasattr(act, "model_dump"):
                            act = act.model_dump()
                        if isinstance(act, dict):
                            actions_taken.append(act)
                else:
                    for action_name, action_args in output_dict.items():
                        if action_name not in ["interacted_element", "current_state"] and action_args is not None:
                            actions_taken.append({"action": action_name, "args": action_args})

        if not actions_taken:
            return

        # Simple keyword extraction from goal
        import string
        words = [w.lower().strip(string.punctuation) for w in goal.split() if len(w.strip(string.punctuation)) > 2]
        wf_id = "_".join(words[:4])

        existing_wf = self.workflows.get(wf_id, {})
        runs = existing_wf.get("runs", 0) + 1
        success_runs = existing_wf.get("success_runs", 0) + 1

        confidence = min(0.5 + (success_runs * 0.1), 0.99)

        workflow_data = {
            "id": wf_id,
            "name": goal,
            "keywords": words,
            "runs": runs,
            "success_runs": success_runs,
            "confidence_score": round(confidence, 2),
            "last_updated": datetime.now().isoformat(),
            "procedural_steps": actions_taken
        }

        self.workflows[wf_id] = workflow_data
        self._save_workflows()
        logger.info(f"✨ Learned new browser workflow: '{goal}' stored in 0xItachi Memory.")

    def format_workflow_hint(self, workflow: dict) -> str:
        """
        Formats a learned workflow into a prompt hint for the Agent.
        """
        steps_str = json.dumps(workflow.get("procedural_steps", []), indent=2)
        return (
            f"REUSABLE WORKFLOW HINT FROM 0xITACHI MEMORY:\n"
            f"You previously completed a similar goal '{workflow['name']}' successfully.\n"
            f"Recommended Action Sequence:\n{steps_str}\n"
            f"Use this sequence as a guide to complete the task faster and avoid extra navigation."
        )
