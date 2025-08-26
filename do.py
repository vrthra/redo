#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple


class Colors:
    def __init__(self):
        if os.isatty(sys.stderr.fileno()) and os.getenv('TERM', 'dumb') != 'dumb':
            self.green, self.bold, self.plain = '\033[32m', '\033[1m', '\033[m'
        else:
            self.green = self.bold = self.plain = ''

# Manages global state for the redo build system.
class RedoState:
    
    def __init__(self):
        self.script_path = Path(__file__).resolve()
        self.start_dir = Path.cwd().resolve()
        self.built_file = self.start_dir / '.do_built'
        self.path_dir = self.start_dir / '.do_built.dir'
        self.depth = os.getenv('DO_DEPTH', '')
        self.colors = Colors()
        
        # Command line options
        self.debug, self.verbose, self.exec_trace = False, False, False
        self.clean = False


def debug_print(state: RedoState, *args):
    if not state.debug: return
    print(*args, file=sys.stderr)


# Split path into directory and basename components.
def split_path(path: str) -> Tuple[str, str]:
    path_obj = Path(path)
    return str(path_obj.parent), path_obj.name


# Find default*.do files in a specific directory matching the target pattern.
# Returns the first existing file found, or None if no match.
def find_dofiles_in_dir(dodir: Path, target_base: str) -> Optional[Path]:
    # Remove 'default.' prefix if present
    if target_base.startswith('default.'):
        dofile = target_base
    else:
        # Remove the first extension to create default pattern
        parts = target_base.split('.', 1)
        dofile = parts[1] if len(parts) > 1 else target_base
    
    # Try increasingly general default.*.do patterns
    while True:
        if dofile.startswith('default.'):
            dofile = dofile[8:]  # Remove 'default.'
        dofile = f'default.{dofile}'
        
        candidate = dodir / dofile
        if candidate.exists():
            return candidate
            
        if dofile == 'default.do': break
        
        # Remove one more extension level
        parts = dofile[8:].split('.', 1)  # Remove 'default.' prefix
        if len(parts) <= 1:
            dofile = 'default.do'
        else:
            dofile = f'default.{parts[1]}'
    
    return None


# Find all possible .do files for a target, searching up the directory tree.
# Returns list of candidate files in order of preference.
def find_dofiles(target: str) -> List[Path]:
    target_path = Path(target)
    candidates = []
    
    # First try exact match
    exact_dofile = Path(f'{target}.do')
    candidates.append(exact_dofile)
    if exact_dofile.exists():
        return candidates
    
    # Try default.*.do files, walking up the directory tree
    current_dir = target_path.parent if target_path.parent != Path('.') else Path.cwd()
    target_base = target_path.name
    
    for _ in range(100):  # Prevent infinite loops
        dofile = find_dofiles_in_dir(current_dir, target_base)
        if dofile:
            candidates.append(dofile)
            return candidates
        
        parent = current_dir.parent
        if parent == current_dir:  # Reached root
            break
        current_dir = parent
    
    return candidates


# Find the best .do file for a target. Returns None if not found.
def find_dofile(target: str) -> Optional[Path]:
    candidates = find_dofiles(target)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


 # Set up the build environment with necessary directories and symlinks.
def setup_build_environment(state: RedoState):
    if not hasattr(setup_build_environment, '_initialized'):
        # Create built file if it doesn't exist
        state.built_file.touch(exist_ok=True)
        
        # Clean up old temporary files
        if state.built_file.exists():
            with open(state.built_file, 'r') as f:
                built_targets = [line.strip() for line in f if line.strip()]
            
            for target in built_targets:
                target_path = Path(target)
                if state.clean:
                    # Remove target and .did files in clean mode
                    for suffix in ['', '.did']:
                        file_to_remove = target_path.with_suffix(target_path.suffix + suffix)
                        file_to_remove.unlink(missing_ok=True)
                
                # Always remove temporary files
                temp_file = target_path.with_suffix(target_path.suffix + '.did.tmp')
                temp_file.unlink(missing_ok=True)
        
        # Set up PATH directory with symlinks
        state.path_dir.mkdir(exist_ok=True)
        
        for cmd in ['redo', 'redo-ifchange', 'redo-whichdo']:
            symlink_path = state.path_dir / cmd
            symlink_path.unlink(missing_ok=True)
            symlink_path.symlink_to(state.script_path)
        
        # Create stub commands
        for cmd in ['redo-ifcreate', 'redo-stamp', 'redo-always', 
                   'redo-ood', 'redo-targets', 'redo-sources']:
            stub_path = state.path_dir / cmd
            stub_path.write_text('#!/bin/sh\n')
            stub_path.chmod(0o755)
        
        # Update PATH
        current_path = os.environ.get('PATH', '')
        os.environ['PATH'] = f'{state.path_dir}:{current_path}'
        
        setup_build_environment._initialized = True


# Execute a .do file with the appropriate environment and arguments.
def run_dofile(state: RedoState, dofile: Path, target: str, base: str, tmp_output: str):
    # Set up environment variables
    env = os.environ.copy()
    env['DO_DEPTH'] = state.depth + '  '
    env['REDO_TARGET'] = str(Path.cwd() / target)
    
    # Read first line to determine how to execute
    try:
        with open(dofile, 'r') as f:
            first_line = f.readline().strip()
    except (IOError, OSError):
        first_line = ''
    
    # Prepare shell options
    shell_opts = []
    if state.verbose:
        shell_opts.append('-v')
    if state.exec_trace:
        shell_opts.append('-x')
    
    if first_line.startswith('#!/'):
        # Execute with specified interpreter
        interpreter = first_line[2:].strip()
        cmd = [f'/{interpreter}', str(dofile), target, base, tmp_output]
    else:
        # Source as shell script
        shell_cmd = ' '.join([f'set {opt}' for opt in shell_opts] + 
                            [f'. {dofile}'])
        cmd = ['sh'] + [f'-{opt}' for opt in shell_opts] + ['-c', shell_cmd]
        env.update({
            'target': target,
            'base': base,
            'tmp_output': tmp_output
        })
    
    # Execute the command
    try:
        result = subprocess.run(cmd, env=env, cwd=dofile.parent, 
                              capture_output=False, check=True)
        return result.returncode
    except subprocess.CalledProcessError as e:
        return e.returncode

# Build a single target by finding and executing its .do file.
# 
# Args:
#     state: Global redo state
#     target_dir: Directory containing the target
#     target_name: Name of the target file
#     
# Returns:
#     True if build successful, False otherwise
def build_target(state: RedoState, target_dir: str, target_name: str) -> bool:
    target_path = Path(target_dir) / target_name
    temp_path = target_path.with_suffix(target_path.suffix + '.redo.tmp')
    did_file = target_path.with_suffix(target_path.suffix + '.did')
    
    # Check if we need to rebuild
    should_build = (
        state.script_path.name == 'redo' or
        (not target_path.exists() or target_path.is_dir()) and
        not did_file.exists()
    )
    
    if not should_build:
        debug_print(state, f"do  {state.depth}{target_path} exists.")
        return True
    
    # Print build message
    print(f'{state.colors.green}do  {state.depth}{state.colors.bold}'
          f'{target_path}{state.colors.plain}', file=sys.stderr)
    
    # Find appropriate .do file
    os.chdir(target_dir or '.')
    dofile = find_dofile(target_name)
    
    if not dofile:
        print(f'do: {target_path}: no .do file ({Path.cwd()})', file=sys.stderr)
        return False
    
    # Determine file extension for default.*.do files
    if dofile.name.startswith('default.'):
        ext = dofile.name[7:]  # Remove 'default'
        ext = ext[:-3] if ext.endswith('.do') else ext  # Remove '.do'
    else:
        ext = ''
    
    # Calculate relative paths
    dofile_dir = dofile.parent
    rel_target = os.path.relpath(target_path, dofile_dir)
    rel_temp = os.path.relpath(temp_path, dofile_dir)
    base = rel_target[:-len(ext)] if ext else rel_target
    
    # Change to .do file directory
    original_cwd = Path.cwd()
    os.chdir(dofile_dir)
    
    try:
        # Create temporary .did file if possible
        did_temp = target_path.with_suffix(target_path.suffix + '.did.tmp')
        try:
            if state.built_file.exists() and target_path.parent.exists():
                did_temp.touch()
        except (IOError, OSError):
            pass
        
        # Execute the .do file
        exit_code = run_dofile(state, dofile, rel_target, base, str(rel_temp))
        
        if exit_code != 0:
            print(f'do: {state.depth}{target_path}: got exit code {exit_code}', 
                  file=sys.stderr)
            # Clean up on failure
            temp_path.unlink(missing_ok=True)
            did_temp.unlink(missing_ok=True)
            did_file.unlink(missing_ok=True)
            return False
        
        # Record successful build
        with open(state.built_file, 'a') as f:
            f.write(f'{target_path}\n')
        
        # Move temp output to final target if it exists
        if temp_path.exists():
            temp_path.rename(target_path)
        
        # Move .did.tmp to .did
        if did_temp.exists():
            did_temp.rename(did_file)
        else:
            did_file.touch()
        
        return True
    
    finally:
        os.chdir(original_cwd)

# Main redo implementation - build all specified targets.
# 
# Args:
#     state: Global redo state
#     targets: List of target files to build
#     
# Returns:
#     True if all builds successful, False otherwise
def redo_main(state: RedoState, targets: List[str]) -> bool:
    original_dir = Path.cwd()
    
    for target in targets:
        # Convert to absolute path
        target_path = Path(target).resolve()
        
        # Change to start directory
        os.chdir(state.start_dir)
        
        # Convert back to relative path from start directory
        try:
            rel_path = target_path.relative_to(state.start_dir)
        except ValueError:
            rel_path = target_path
        
        # Split into directory and filename
        target_dir = str(rel_path.parent) if rel_path.parent != Path('.') else ''
        target_name = rel_path.name
        
        # Build the target
        success = build_target(state, target_dir, target_name)
        if not success:
            return False
    
    return True


# Implementation of redo-whichdo command - show possible .do files.
def whichdo_main(target: str) -> None:
    candidates = find_dofiles(target)
    for candidate in candidates:
        print(candidate)


# Clean up stamp files if in clean mode.
def cleanup_on_exit(state: RedoState):
    if state.clean and state.built_file.exists():
        print("do: Removing stamp files...", file=sys.stderr)
        
        with open(state.built_file, 'r') as f:
            built_targets = [line.strip() for line in f if line.strip()]
        
        for target in built_targets:
            did_file = Path(target).with_suffix('.did')
            did_file.unlink(missing_ok=True)


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='A minimal alternative to djb redo',
        add_help=False  # We'll handle -h manually for compatibility
    )
    
    parser.add_argument('-d', '--debug', action='store_true',
                       help='print extra debug messages')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help="run .do files with 'set -v'")
    parser.add_argument('-x', '--exec-trace', action='store_true',
                       help="run .do files with 'set -x'")
    parser.add_argument('-c', '--clean', action='store_true',
                       help='clean up all old targets before starting')
    parser.add_argument('-j', '--jobs', type=int,
                       help='ignored for compatibility with real redo')
    parser.add_argument('-h', '-?', '--help', action='store_true',
                       help='show this help message')
    parser.add_argument('targets', nargs='*',
                       help='targets to build')
    
    return parser.parse_args()


def print_usage():
    """Print usage information."""
    usage = """usage: do [-d] [-x] [-v] [-c] <targets...>
  -d  print extra debug messages (mostly about dependency checks)
  -v  run .do files with 'set -v'
  -x  run .do files with 'set -x'
  -c  clean up all old targets before starting

  Note: do is an implementation of redo that does *not* check dependencies.
  It will never rebuild a target it has already built, unless you use -c.
"""
    print(usage, file=sys.stderr)


def main():
    """Main entry point."""
    # Determine command name
    cmd_name = Path(sys.argv[0]).name
    
    # Parse arguments
    args = parse_arguments()
    
    if args.help:
        print_usage()
        sys.exit(0)
    
    # Initialize state
    state = RedoState()
    state.debug = args.debug
    state.verbose = args.verbose
    state.exec_trace = args.exec_trace
    state.clean = args.clean
    
    # Set default target if none specified and command is 'do' or 'redo'
    targets = args.targets
    if not targets and cmd_name in ('do', 'redo'):
        targets = ['all']
    
    # Handle different command modes
    try:
        if cmd_name == 'redo-whichdo':
            if targets:
                whichdo_main(targets[0])
            sys.exit(0)
        
        elif cmd_name in ('do', 'redo', 'redo-ifchange'):
            # Check if this is the top-level invocation
            is_top_level = 'DO_BUILT' not in os.environ
            
            if is_top_level:
                # Set up environment for sub-processes
                os.environ['DO_BUILT'] = str(state.built_file)
                os.environ['DO_STARTDIR'] = str(state.start_dir)
                os.environ['DO_PATH'] = str(state.path_dir)
                
                # Set up build environment
                setup_build_environment(state)
                
                # Print incremental mode message
                if not state.clean and state.built_file.exists():
                    print("do: Incremental mode. Use -c for clean rebuild.", 
                          file=sys.stderr)
            
            # Build targets
            success = redo_main(state, targets)
            
            # Clean up if this is the top level
            if is_top_level:
                cleanup_on_exit(state)
            
            sys.exit(0 if success else 1)
        
        else:
            print(f"do: '{cmd_name}': unexpected redo command", file=sys.stderr)
            sys.exit(99)
    
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"do: error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
