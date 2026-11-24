# SPDX-License-Identifier: AGPL-3.0-only
"""Model write boundary shared by DAT content, assignments and linked diagrams."""

class WorkflowTrackedContent:
    _workflow_dat_path = "dat_id"
    _workflow_content_fields = None
    _workflow_allow_terminal_metadata = False
    _workflow_require_referent_gate = False

    def workflow_dat_id(self):
        value = self
        for part in self._workflow_dat_path.split("."):
            value = getattr(value, part, None)
            if value is None:
                break
        return value

    def save(self, *args, **kwargs):
        from .graph_engine import content_mutation
        fields = kwargs.get("update_fields")
        if fields is not None and self._workflow_content_fields is not None and not set(fields).intersection(self._workflow_content_fields):
            return super().save(*args, **kwargs)
        with content_mutation(
            self.workflow_dat_id(),
            allow_terminal_metadata=self._workflow_allow_terminal_metadata,
            require_referent_gate=self._workflow_require_referent_gate,
        ):
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .graph_engine import content_mutation

        with content_mutation(self.workflow_dat_id(), require_referent_gate=self._workflow_require_referent_gate):
            return super().delete(*args, **kwargs)
