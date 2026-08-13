# Elyndra 0.7.23-alpha

## Ethics review v2 and offline multilingual starter lexicon

Elyndra 0.7.23-alpha strengthens the immutable constitutional review with explicit categories for self-harm or crisis, violence or homicide, child sexual abuse material, malicious cyber activity, privacy abuse, fraud, sabotage, environmental harm and ambiguous concealment. Explicit deterministic blocks run before the router, supervised actions and the language model.

Ambiguous requests may receive a secondary local tutor review through the configured language engine. The tutor returns a strict bounded classification and cannot weaken a deterministic block, authorize an action, access tools or change the constitution. If the tutor is unavailable or remains uncertain, Elyndra fails closed and offers neutral safe alternatives. Raw ethics prompts are not stored in `assistant_ethics_reviews`; schema 31 adds confidence and tutor-review metadata.

The release also adds an offline multilingual starter lexicon for Spanish, English, Japanese, Chinese, Italian, French, Portuguese and German. It ships as versioned package data with license and SHA-256 metadata, supports deterministic exact lookup and compact translations or glosses, and is exposed through `dictionary lookup`, `dictionary status`, `dictionary languages`, `dictionary.lookup` and loopback-only API endpoints. It is deliberately a small starter lexicon, not a complete dictionary, grammar engine or replacement for generative translation.

Elyndra now registers 101 skills. The dictionary path uses no model or network. Ollama remains a replaceable tutor for language and ambiguous secondary review; Elyndra retains authority over ethics, permissions, memory, tools and approvals.

## Safety boundaries

- Explicit self-harm, homicide and child sexual abuse material requests are redirected without consulting the model.
- The local tutor may increase caution but never lower a deterministic restriction.
- Ambiguous concealment requests remain blocked when the tutor is unavailable or uncertain.
- Defensive, authorized security requests remain allowed with an explicit scope warning.
- No online dictionary lookup, silent download, autonomous action or background work is introduced.
