; dodge.asm - "ROCK DODGE" for TAPBOY-8.
;
; 8 lanes (0..7). Each round a rock falls into a pseudo-random lane, and the coin sits in the opposite
; lane (rock lane XOR 4). The round shows the rock row on the OUT pins (one-hot byte, VALID = 1); in the
; next beat the CPU reads the buttons:  bit 0 = step left,  bit 1 = step right  (lanes wrap around).
; Then:  player on the rock lane  -> crash, game over
;        player on the coin lane  -> +3 (coin) +1 (survived)
;        otherwise                -> +1 (survived)
; After ROUNDS rounds (or a crash) the score is printed (VALID = 1) and the CPU halts; from then on the
; OUT pins keep showing the score (ACC), so the last beat of a run carries the final score.
;
; Pseudo-random lanes: an 8-bit Galois LFSR (taps 0x1D), seeded with 0xA5.

        .equ POS     0x00       ; player lane 0..7
        .equ SEED    0x01       ; LFSR state
        .equ SCORE   0x02
        .equ ROUNDS  0x03       ; rounds left
        .equ LANE    0x04       ; rock lane this round
        .equ BTN     0x05       ; buttons read this round
        .equ TMP     0x06
        .equ ONEHOT  0xF0       ; table 0xF0..0xF7 = 1, 2, 4, ..., 128 (RAM starts all zero)

        .equ NROUNDS 16
        .equ START   3

init:   LD  #1                  ; build the one-hot table with a doubling loop, indexed by B
        LDB #0
mk:     ST  [ONEHOT+B]
        ADD [ONEHOT+B]          ; ACC = 2 * ACC
        SWAP
        ADD #1
        SWAP                    ; B = B + 1
        JNZ mk                  ; ACC becomes 0 after 0x80 + 0x80
        LD  #START
        ST  [POS]
        LD  #0xA5
        ST  [SEED]
        LD  #NROUNDS
        ST  [ROUNDS]

round:  LD  [SEED]              ; --- LFSR step: seed = (seed << 1) ^ (0x1D if bit 7 was set)
        AND #0x80
        ST  [TMP]
        LD  [SEED]
        ADD [SEED]
        ST  [SEED]
        LD  [TMP]
        JZ  nofb
        LD  [SEED]
        XOR #0x1D
        ST  [SEED]
nofb:   LD  [SEED]              ; --- rock lane = seed & 7, show the rock row
        AND #7
        ST  [LANE]
        LDB [LANE]
        LD  [ONEHOT+B]
        OUT
        IN                      ; --- the player's buttons for this round
        ST  [BTN]
        AND #1
        JZ  noleft
        LD  [POS]
        ADD #7
        AND #7
        ST  [POS]
noleft: LD  [BTN]
        AND #2
        JZ  noright
        LD  [POS]
        ADD #1
        AND #7
        ST  [POS]
noright: LD [POS]               ; --- crash / coin / survive
        XOR [LANE]
        JZ  over
        XOR #4
        JNZ survive
        LD  [SCORE]
        ADD #3
        ST  [SCORE]
survive: LD [SCORE]
        ADD #1
        ST  [SCORE]
        LD  [ROUNDS]
        SUB #1
        ST  [ROUNDS]
        JNZ round
over:   LD  [SCORE]
        OUT
        HALT
