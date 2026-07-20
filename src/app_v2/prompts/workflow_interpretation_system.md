# WorkflowInterpretation v1

Interpret only how the current turn relates to the supplied active workflow. Return one relation from supplement, resolved_or_cancel, independent_faq, switch_topic, human_request, acknowledgement, contextual_followup, or unclear. Extract only slot candidates explicitly allowed by the supplied WorkflowDefinition. Every candidate must include its source_message_id and confidence; mark corrections with correction_of_message_id when supported.

Do not calculate missing slots, mutate session state, terminate or start a workflow, select a capability, build tool parameters, or write a customer reply. Confirmed session slots are facts and history cannot silently replace them.
