"""One process-local approval for bounded read-only stages of one project.

Each stage still uses its ordinary TransferSession limits. This owner-approved
scope can issue at most TASK_STAGES such grants, all for the same captured index,
policy and recipient, and none can outlive the overall deadline. Follow-up
questions in the same live project session reuse it (S9) while all of that
holds; a changed file, policy, model or recipient, an exhausted budget, the
deadline or a cancellation ends it, and the next question asks again. It cannot
authorize test execution, edits, writes, deletion or resumed-session access.
"""
import os

from .analysis_contract import canonical
from .execution import current as current_execution
from .material_grant import GrantDenied, MaterialGrant, MAX_DEADLINE_SECONDS
from .project_step import MAX_STAGES, MAX_STEPS, TOTAL_BYTES


TASK_STAGES = 2 * MAX_STAGES          # all questions of one approved task together; one question still stops at MAX_STAGES
TASK_SECONDS = MAX_STAGES * MAX_DEADLINE_SECONDS
TASK_REQUESTS = TASK_STAGES * MAX_STEPS
TASK_BYTES = TASK_STAGES * TOTAL_BYTES


class ProjectTaskConsent:
    def __init__(self, project, question, *, clock):
        self._project = project
        self._policy = project.policy
        self._policy_hash = self._policy.fingerprint()
        self._execution = canonical(current_execution())
        self._pid = os.getpid()
        self._state = self._binding(project)
        self._question = question
        self._clock = clock
        self._deadline = None
        self._approved = False
        self._closed = False
        self._stages = 0

    @staticmethod
    def _binding(project):
        # Include the full fd identities and scan outcome, not only the legacy
        # manifest's path/size/mtime tuple. A removed or newly denied entry must
        # not silently turn into a different project under the original yes.
        return canonical({"id": project.project_id, "root": project.root,
                          "manifest": project.manifest_sha256(),
                          "files": [(entry.path, entry.identity) for entry in project.files],
                          "stats": project.stats, "complete": project.complete,
                          "reason": project.reason})

    def _validate(self, project):
        if self._closed or os.getpid() != self._pid:
            self.close()
            raise GrantDenied("GRANT_INVALID")
        if self._approved and self._clock() > self._deadline:
            self.close()
            raise GrantDenied("GRANT_EXPIRED")
        try:
            if project is not self._project or project.policy is not self._policy:
                raise ValueError()
            self._policy.recheck()
            if (self._policy.fingerprint() != self._policy_hash
                    or canonical(current_execution()) != self._execution):
                raise ValueError()
            # Cached Project.files is not evidence that the sources on disk
            # remain the captured version. This runs before every hand-off,
            # including after pacing/session startup, and before local reads.
            project.scan()
            self._policy.recheck()
            if (project.policy is not self._policy or self._policy.fingerprint() != self._policy_hash
                    or canonical(current_execution()) != self._execution):
                raise ValueError()
            if self._binding(project) != self._state:
                self.close()
                raise GrantDenied("SOURCE_CHANGED")
        except (OSError, ValueError, TypeError, AttributeError):
            self.close()
            raise GrantDenied("GRANT_INVALID") from None
        if self._approved and self._clock() > self._deadline:
            self.close()
            raise GrantDenied("GRANT_EXPIRED")

    def approve(self, project):
        """The trusted UI calls once, after showing the whole scope and receiving yes."""
        if self._approved:
            self.close()
            raise GrantDenied("GRANT_INVALID")
        self._validate(project)
        # Owner thinking time before the one approval is outside the task TTL.
        self._deadline = self._clock() + TASK_SECONDS
        self._approved = True

    def check(self, project):
        if not self._approved:
            raise GrantDenied("GRANT_INVALID")
        self._validate(project)

    def reusable(self, project):
        """True while a follow-up question may use this approval without a new prompt; never raises.

        Any refusal closes the scope for good, so a later question always asks again."""
        if not self._approved or self._closed:
            return False
        try:
            self._validate(project)
        except GrantDenied:
            return False
        if self._stages >= TASK_STAGES:
            self.close()
            return False
        return True

    def follow_up(self, project, question):
        """Bind the next stages to a new question of the same task (the shown scope already covered follow-ups)."""
        self.check(project)
        if type(question) is not str or not question:
            self.close()
            raise GrantDenied("GRANT_INVALID")
        self._question = question

    @property
    def stages_left(self):
        return max(0, TASK_STAGES - self._stages)

    @property
    def seconds_left(self):
        if not self._approved:
            return 0
        return max(0, int(self._deadline - self._clock()))

    def next_stage(self, project):
        self.check(project)
        if self._stages >= TASK_STAGES:
            self.close()
            raise GrantDenied("BUDGET_EXHAUSTED")
        self._stages += 1
        return MaterialGrant(project.project_id, project.manifest_sha256(), self._question[:256],
                             min(self._deadline, self._clock() + MAX_DEADLINE_SECONDS),
                             max_requests=MAX_STEPS, max_bytes=TOTAL_BYTES)

    def close(self):
        self._closed = True
