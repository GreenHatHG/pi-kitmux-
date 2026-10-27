## Comments: landmines only, and only the "why"

Default is no comment. Write one only if leaving it out means the next person breaks something.
One test: could they find this fact anywhere else? Yes → it belongs in a name or a type. No → keep it, on the exact line where it bites.

- Never restate the code. `// add 1` above `i++` is always noise.
- Name it, don't narrate it. `retryBudget` beats `// how many retries we allow`. A test called `loses_data_when_write_fails` states the rule with no comment at all.
- Keep the traps: an order that looks swappable but isn't, a number that came from a real incident, a third-party quirk, a failure that goes quiet instead of loud.
- Line-level, one line. A note on the line that will break — not an essay above the function.
- Prefer whatever can't go stale. A name or a type outlives a comment.
- Neither fits? Then the comment stays. That is what it is for.

Words: plain daily English, A2/B1. Chatty, one short sentence. No textbook voice.
