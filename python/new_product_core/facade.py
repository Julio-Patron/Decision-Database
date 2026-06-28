import json
import subprocess
from typing import Dict, Any

from orchestrator_cli.schemas import DecisionInput, DecisionContext

class EvaluatorFacade:
    def __init__(self, binary_path: str):
        self.binary_path = binary_path

    def evaluate(self, input_data: DecisionInput) -> DecisionContext:
        """
        Evaluates a rule against a context by calling the underlying Rust binary.
        """
        payload = input_data.model_dump_json()

        try:
            result = subprocess.run(
                [self.binary_path],
                input=payload,
                text=True,
                capture_output=True,
                check=True
            )
            output_json = json.loads(result.stdout)
            return DecisionContext(**output_json)
        except subprocess.CalledProcessError as e:
            return DecisionContext(
                success=False,
                error=f"Engine execution failed: {e.stderr}"
            )
        except json.JSONDecodeError as e:
            return DecisionContext(
                success=False,
                error=f"Failed to parse engine output: {str(e)}"
            )
