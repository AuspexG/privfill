# Event registration

This is a placeholder master. The assistant edits and reviews only this file;
privfill fills in the real values locally.

| | |
|---|---|
| Name | {{full name}} |
| Date of birth | {{date of birth|D/M/YYYY}} |
| Phone | {{phone}} |
| Phone, last four digits | {{phone|last4}} |
| Email | {{email}} |

Generate the output (the master stays untouched; outputs go to their own folder):

    privfill.py render 'examples/{{full name}}_registration.md' 'out/{{full name}}_registration.md'
