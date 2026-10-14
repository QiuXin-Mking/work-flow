# Backend context

- Identify the API or event entry point, validation, service logic, persistence calls, and outbound effects involved in the Bug.
- Preserve existing error semantics and response contracts. If a new failure path is needed, make it explicit and test it.
- Check database schema and migration implications before changing stored data structures.
- Keep credentials and personal data out of logs and returned errors.
- For external calls, distinguish transport failure, remote rejection, and local mapping failure; do not silently convert any of them into success.
