; hello.asm - prints "Hello, TAP-721!" then a newline, and halts.
; Every character is LD #c (ACC = c) followed by OUT (VALID = 1 for one beat).
        .print "Hello, TAP-721!\n"
        HALT
