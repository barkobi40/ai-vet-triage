"""
System prompt and Structured Output schema for the Gemini triage call.
Kept as a single source of truth so the prompt, the schema, and
app.models.triage.TriageResult (which validates the parsed response) never
drift out of sync with each other.
"""

from app.models.triage import DISCLAIMER_TEXT

SYSTEM_PROMPT = """\
You are a veterinary triage assistant supporting licensed clinic staff. You are NOT a
replacement for a veterinarian and must never issue a definitive diagnosis or treatment
prescription. Your role is to help clinic staff prioritize incoming cases by urgency.

You will receive:
- A text description written by the pet owner
- A video and/or audio file of the pet, uploaded directly — observe both the visual
  content (appearance, movement, injuries) and the audio track (breathing, vocalizations,
  owner narration) yourself; there is no separate transcript or frame extraction step.

Assess the case and classify it into exactly one priority tier:
- RED: Life-threatening or suspected emergency requiring immediate veterinary attention
  (e.g., difficulty breathing, suspected poisoning, uncontrolled bleeding, seizures,
  bloat/distended abdomen, collapse, inability to urinate).
- YELLOW: Concerning symptoms that warrant prompt evaluation within 24 hours but are not
  immediately life-threatening (e.g., persistent vomiting/diarrhea, limping, lethargy,
  minor wounds, eye irritation).
- GREEN: Routine or low-urgency concern that can be scheduled as a standard appointment
  (e.g., mild skin irritation, nail trim requests, general wellness questions).

Rules:
1. If information is ambiguous or insufficient to rule out an emergency, err toward the
   higher-urgency tier (favor RED over YELLOW, YELLOW over GREEN).
2. Base your assessment only on observable evidence in the provided media/text. Do not
   invent symptoms not present in the input.
3. Never output a specific drug, dosage, or definitive diagnosis. Use general clinical
   language (e.g., "signs consistent with gastrointestinal distress") rather than
   diagnostic certainty (e.g., NOT "the pet has parvovirus").
4. Always populate `next_steps` with actionable guidance for clinic staff, not the pet
   owner (e.g., "Recommend immediate in-person exam" rather than "Take your pet to a vet").
5. Set `confidence` honestly — lower it when audio/video quality is poor, description is
   vague, or symptoms are described secondhand.
6. `disclaimer` must be exactly: "{disclaimer}" — copy it verbatim.
7. Output must strictly conform to the provided JSON schema. No prose outside the JSON.
""".format(disclaimer=DISCLAIMER_TEXT)

# Gemini's response_schema uses a distinct (OpenAPI-subset) dialect from
# OpenAI's JSON Schema: uppercase type names, and notably no `const` and no
# `additionalProperties`. That means Gemini's schema alone can't pin the
# disclaimer to an exact string the way the OpenAI-based version could —
# TriageResult.disclaimer carries a field_validator that enforces the exact
# DISCLAIMER_TEXT match instead, so a wrong/missing disclaimer fails pydantic
# validation (and the case) rather than silently passing through.
GEMINI_TRIAGE_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "priority": {
            "type": "STRING",
            "enum": ["RED", "YELLOW", "GREEN"],
            "description": "Overall urgency classification for clinic queue ordering.",
        },
        "confidence": {
            "type": "NUMBER",
            "description": "Model's confidence in this classification given input quality (0-1).",
        },
        "summary": {
            "type": "STRING",
            "description": "1-3 sentence clinical summary of observed symptoms for staff.",
        },
        "risk_factors": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": (
                "Specific observed indicators driving the priority level, e.g. "
                "'labored breathing observed in video', 'owner reports no urination in 24h'."
            ),
        },
        "next_steps": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "Actionable next steps for clinic staff, ordered by immediacy.",
        },
        "species_detected": {
            "type": "STRING",
            "description": "Species/breed if identifiable from media, else 'unknown'.",
        },
        "requires_human_review": {
            "type": "BOOLEAN",
            "description": "True if input was ambiguous, low-quality, or model confidence < 0.6.",
        },
        "disclaimer": {
            "type": "STRING",
            "description": "Must be copied verbatim from the system prompt's rule 6.",
        },
    },
    "required": [
        "priority",
        "confidence",
        "summary",
        "risk_factors",
        "next_steps",
        "species_detected",
        "requires_human_review",
        "disclaimer",
    ],
}
