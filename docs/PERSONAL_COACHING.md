# Personal coaching and wellbeing

Elyndra 0.8.2-alpha adds bounded local wellbeing tracking and reviewed coaching
plans. The feature is an organizational and reflection aid, not a clinical
system.

## Check-ins

A check-in may record a local date and 1–5 values for mood, energy, stress and
focus. Optional values include sleep hours, sleep quality, hydration, nutrition,
activity minutes and a bounded note. Records remain in SQLite and are queried in
bounded date windows.

Elyndra can calculate deterministic averages and simple observations. It does not infer a diagnosis, prescribe treatment or claim that a numerical trend proves
a medical or psychological condition.

## Coaching plans

A coaching plan has a title, focus, objective, start date, optional review date
and between one and twelve explicit actions. Plans and actions change state only
through owner-approved operations. There is no automatic progression, reminder
delivery, intervention or model-controlled action.

## Conversational route

Common requests such as `¿Cómo he estado esta semana?` use the local wellbeing
repository before Ollama. The same route is available in CLI and web chat.
Emergency, self-harm and other constitutional safety routes remain ahead of this
summary route.

## Professional boundary

The feature may help the owner notice patterns, prepare questions for a
professional and organize healthy routines. It must not present Elyndra as a
physician, psychologist, dietitian or emergency service. Urgent or high-risk
situations continue through the dedicated safety and first-aid paths.
