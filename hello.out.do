#!/bin/sh
# Build script for hello.out
# Arguments: $1=target, $2=basename, $3=temp_output_file
# $1 = hello.out (target name)
# $2 = hello (basename without extension)
# $3 = hello.out.redo.tmp (temporary output file)

# Compile hello.c into the temporary output file
gcc -o "$3" hello.c

# The redo system will automatically move $3 to the final target (hello.out)
