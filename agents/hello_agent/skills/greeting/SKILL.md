---
name: greeting
description: Greet people warmly in their language and at the right time of day. Use when the user says hello, asks for a greeting or welcome message, or writes a greeting in another language.
---

# Greeting

1. If the user names a city or timezone, call `current_time` for it and pick
   "good morning" (05:00-11:59), "good afternoon" (12:00-17:59) or
   "good evening" (18:00-04:59). Otherwise use a time-neutral greeting.
2. Reply in the language the user wrote in. Common greetings are listed in
   `references/greetings.md`; read it with `read_skill_file` when you need one.
3. Keep it to one or two sentences, then offer help with one concrete example
   of what you can do (tell the time somewhere, count words in a text).
