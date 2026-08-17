#!/bin/bash
set -u -o pipefail

if (( $# != 0 )); then
    echo "Usage: $0" >&2
    exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(dirname -- "$script_dir")
output_dir="$repo_dir/outputs"
request_file="$output_dir/.open-output-folder.request"
ack_file="$output_dir/.open-output-folder.ack"
picker_request_file="$output_dir/.windows-folder-picker.request"
picker_response_file="$output_dir/.windows-folder-picker.response"

mkdir -p -- "$output_dir"
windows_output=$(wslpath -w "$output_dir") || {
    echo "Could not translate SecureScan outputs path for Windows Explorer." >&2
    exit 1
}

if ! command -v explorer.exe >/dev/null 2>&1; then
    echo "Windows Explorer is unavailable from this WSL session." >&2
    exit 1
fi

write_picker_response() {
    local token=$1
    local status=$2
    local payload=${3-}
    local temporary_response="$picker_response_file.$$"

    if [[ -n "$payload" ]]; then
        printf '%s\t%s\t%s\n' "$token" "$status" "$payload" > "$temporary_response"
    else
        printf '%s\t%s\n' "$token" "$status" > "$temporary_response"
    fi
    mv -f -- "$temporary_response" "$picker_response_file"
}

run_windows_folder_picker() {
    local token=$1
    local picker_output picker_status status payload

    # This early response distinguishes a live helper with a dialog awaiting
    # user input from a helper which was never started.
    write_picker_response "$token" "opened"

    if ! command -v powershell.exe >/dev/null 2>&1; then
        payload=$(printf '%s' 'Windows PowerShell is unavailable from this WSL session.' | base64 -w 0)
        write_picker_response "$token" "error" "$payload"
        return
    fi

    # Shell.Application is present on supported Windows versions, has no
    # WinForms assembly/UI-thread dependency, returns filesystem/UNC paths
    # directly, and reports cancellation as a null result. 0x41 combines
    # RETURNONLYFSDIRS with the newer resizable dialog style. No owner window
    # or focus-stealing call is used: Windows foreground-activation policy can
    # still place the dialog behind the browser, and AppActivate/hidden-form
    # workarounds are timing-sensitive rather than reliable.
    picker_output=$(powershell.exe -NoProfile -STA -Command '
$ErrorActionPreference = "Stop"
try {
    $shell = New-Object -ComObject Shell.Application
    $folder = $shell.BrowseForFolder(0, "Select a folder to scan", 0x41, 0)
    if ($null -eq $folder) {
        [Console]::Out.WriteLine("cancel")
        exit 0
    }
    $path = $folder.Self.Path
    if ([string]::IsNullOrWhiteSpace($path)) {
        [Console]::Out.WriteLine("cancel")
        exit 0
    }
    [Console]::Out.WriteLine("selected")
    [Console]::Out.WriteLine(
        [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($path))
    )
} catch {
    [Console]::Out.WriteLine("error")
    [Console]::Out.WriteLine(
        [Convert]::ToBase64String(
            [Text.Encoding]::UTF8.GetBytes($_.Exception.Message)
        )
    )
}
' 2>/dev/null | tr -d '\r')
    picker_status=$?
    if (( picker_status != 0 )); then
        payload=$(printf '%s' 'Windows folder picker failed to start.' | base64 -w 0)
        write_picker_response "$token" "error" "$payload"
        return
    fi

    status=$(printf '%s\n' "$picker_output" | sed -n '1p')
    payload=$(printf '%s\n' "$picker_output" | sed -n '2p')
    case "$status" in
        selected)
            if [[ -n "$payload" ]]; then
                write_picker_response "$token" "selected" "$payload"
            else
                payload=$(printf '%s' 'Windows folder picker returned an empty path.' | base64 -w 0)
                write_picker_response "$token" "error" "$payload"
            fi
            ;;
        cancel)
            write_picker_response "$token" "cancel"
            ;;
        error)
            write_picker_response "$token" "error" "$payload"
            ;;
        *)
            payload=$(printf '%s' 'Windows folder picker returned an invalid response.' | base64 -w 0)
            write_picker_response "$token" "error" "$payload"
            ;;
    esac
}

# Ignore a request left behind by an earlier GUI/helper session. Only a new
# button click during this helper's lifetime may launch Explorer.
last_token=""
if [[ -f "$request_file" ]]; then
    IFS= read -r last_token < "$request_file" || last_token=""
fi

last_picker_token=""
if [[ -f "$picker_request_file" ]]; then
    IFS= read -r last_picker_token < "$picker_request_file" || last_picker_token=""
fi

while true; do
    token=""
    if [[ -f "$request_file" ]]; then
        IFS= read -r token < "$request_file" || token=""
    fi

    if [[ "$token" != "$last_token" && "$token" =~ ^[0-9a-f]{32}$ ]]; then
        last_token="$token"
        # explorer.exe can open the folder successfully yet return late or with a
        # non-zero status through WSL interop. Acknowledge that the fixed-path
        # request was consumed and dispatched; the GUI times out only when no
        # live helper ever sees the token.
        explorer.exe "$windows_output" >/dev/null 2>&1 &
        temporary_ack="$ack_file.$$"
        if printf '%s\n' "$token" > "$temporary_ack"; then
            mv -f -- "$temporary_ack" "$ack_file"
        else
            rm -f -- "$temporary_ack"
        fi
    fi


    picker_token=""
    if [[ -f "$picker_request_file" ]]; then
        IFS= read -r picker_token < "$picker_request_file" || picker_token=""
    fi

    if [[ "$picker_token" != "$last_picker_token" && "$picker_token" =~ ^[0-9a-f]{32}$ ]]; then
        last_picker_token="$picker_token"
        run_windows_folder_picker "$picker_token"
    fi

    sleep 0.2
done
