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

    def _extract_tokens(self, text: str) -> List[str]:
        import re
        import string
        STOP_WORDS = {"go", "to", "the", "and", "for", "read", "what", "they", "are", "about", "with", "from", "get", "find", "http", "https", "www", "com", "org"}
        cleaned = re.sub(r'https?://(www\.)?', '', text.lower())
        raw_words = re.split(r'[\s/._,\-\?:]+', cleaned)
        tokens = [w.strip(string.punctuation) for w in raw_words if len(w.strip(string.punctuation)) >= 3 and w.strip(string.punctuation) not in STOP_WORDS]
        return tokens

    def find_matching_workflow(self, goal: str) -> Optional[dict]:
        """
        Finds a learned workflow matching the given goal keywords.
        """
        query_tokens = set(self._extract_tokens(goal))
        if not query_tokens:
            return None

        best_match = None
        highest_score = 0.0

        for wf_id, wf in self.workflows.items():
            wf_tokens = set(self._extract_tokens(wf.get("name", "")))
            wf_tokens.update(self._extract_tokens(" ".join(wf.get("keywords", []))))
            
            if not wf_tokens:
                continue

            match_count = 0
            for q_tok in query_tokens:
                if any(q_tok in wf_tok or wf_tok in q_tok for wf_tok in wf_tokens):
                    match_count += 1

            score = match_count / max(len(query_tokens), 1)
            logger.debug(f"Match check: '{wf['name']}' -> count={match_count}/{len(query_tokens)}, score={score:.2f}")
            if score > highest_score:
                highest_score = score
                best_match = wf

        if best_match and highest_score >= 0.3:
            logger.info(f"🧠 Persistent Memory Match Found! Workflow: '{best_match['name']}' (Confidence: {best_match['confidence_score']*100:.0f}%)")
            return best_match

        return None

    def save_workflow_from_history(self, goal: str, history: any):
        """
        Extracts action steps from an AgentHistoryList and stores them as a learned workflow.
        """
        if not history:
            return

        actions_taken = []
        if hasattr(history, "model_actions") and callable(history.model_actions):
            try:
                actions_taken = history.model_actions()
            except Exception:
                actions_taken = []

        if not actions_taken and hasattr(history, "all_model_outputs"):
            for model_output in history.all_model_outputs:
                output_dict = {}
                if hasattr(model_output, "model_dump"):
                    output_dict = model_output.model_dump()
                elif isinstance(model_output, dict):
                    output_dict = model_output
                
                if output_dict:
                    if "action" in output_dict and isinstance(output_dict["action"], list):
                        for act in output_dict["action"]:
                            if hasattr(act, "model_dump"):
                                act = act.model_dump()
                            if isinstance(act, dict):
                                actions_taken.append(act)
                    else:
                        for action_name, action_args in output_dict.items():
                            if action_name not in ["interacted_element", "current_state"] and action_args is not None:
                                actions_taken.append({action_name: action_args})

        if not actions_taken:
            logger.warning(f"⚠️ Could not extract actions from history to save workflow for '{goal}'.")
            return

        # Sanitize actions to strip out non-serializable objects like DOMHistoryElement and exclude 'done'
        clean_actions = []
        for step in actions_taken:
            if isinstance(step, dict):
                clean_step = {}
                for k, v in step.items():
                    if k not in ["interacted_element", "current_state", "result", "done"]:
                        try:
                            # Test JSON serializability
                            json.dumps(v)
                            clean_step[k] = v
                        except Exception:
                            pass
                if clean_step:
                    clean_actions.append(clean_step)

        if not clean_actions:
            return

        # Simple keyword extraction from goal
        import string
        words = self._extract_tokens(goal)
        wf_id = "_".join(words[:4]) if words else "workflow_" + str(hash(goal))

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
            "procedural_steps": clean_actions
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
