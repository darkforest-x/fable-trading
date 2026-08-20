"""Pattern Teacher: a detector used to find candidates, never to fire them.

YOLO is retained in this project as a teacher and explicitly not as the final
tip trigger. The number behind that decision: the old detector reproduces its
own boxes at 62-72% given full context and at 9-10% at the tip. Whatever it
knows, it knows about completed patterns.

So the teacher's job here is narrow and stated in the protocol:

    propose()  hand back CandidateProposals for study
    embed()    hand back a vector for retrieval and similarity work

Neither returns a signal, and CandidateProposal refuses to be marked
production_eligible at all, so "teacher output reaches the executor" is not a
policy that can be violated by forgetting -- it is a constructor that raises.

Registration, not duplication: the weights stay where they already are and are
identified in artifacts/registry.yaml by SHA-256. owner_v10_chain's digest was
cross-checked three ways -- this repository's copy, the Windows 3060 copy, and
C:\\fable\\base_hts.pt -- and all three agree.
"""
from yoyo.layers.l1_detection.teacher.protocol import (
    Embedding,
    PatternTeacher,
    TeacherRegistrationError,
    describe_teacher,
    resolve_teacher_artifact,
)

__all__ = [
    "Embedding",
    "PatternTeacher",
    "TeacherRegistrationError",
    "describe_teacher",
    "resolve_teacher_artifact",
]
