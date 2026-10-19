; hello.asm - prints "Hello, tapnet/2!" then a newline, and halts.
; Every character is LD #c (ACC = c) followed by OUT (VALID = 1 for one beat).
        .print "Hello, tapnet/2!\n"
        HALT
