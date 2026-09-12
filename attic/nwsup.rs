//! House supervisor. Lids from the plan.
//! Seccomp BPF lives in lids.c (same table the C twin uses).

use std::env;
use std::ffi::CString;
use std::io::Write;
use std::os::raw::{c_char, c_int};
use std::process;

const NW_LID_SECCOMP: u32 = 0x01;
const NW_LID_NEWNS: u32 = 0x04;
const NW_LID_NEWNET: u32 = 0x08;
const CLONE_NEWNS: c_int = 0x00020000;
const CLONE_NEWNET: c_int = 0x40000000;

extern "C" {
    fn unshare(flags: c_int) -> c_int;
    fn execv(path: *const c_char, argv: *const *const c_char) -> c_int;
    fn nw_apply_house_seccomp() -> c_int;
}

fn say(s: &str) {
    let _ = std::io::stderr().write_all(format!("[nw-sup] {s}\n").as_bytes());
}

fn die(s: &str) -> ! {
    let err = std::io::Error::last_os_error();
    let _ = std::io::stderr().write_all(format!("[nw-sup] FAIL {s} {err}\n").as_bytes());
    process::exit(72);
}

fn lid_netns() {
    unsafe {
        if unshare(CLONE_NEWNET) != 0 {
            die("unshare net");
        }
    }
    say("lid newnet");
}

fn lid_newns() {
    unsafe {
        if unshare(CLONE_NEWNS) != 0 {
            die("unshare ns");
        }
    }
    say("lid newns");
}

fn lid_seccomp() {
    unsafe {
        if nw_apply_house_seccomp() != 0 {
            die("house seccomp");
        }
    }
    say("lid seccomp");
}

fn main() {
    let mut args = env::args();
    let _ = args.next();
    let path = args.next().unwrap_or_else(|| die("argv path"));
    let name = args.next().unwrap_or_else(|| die("argv name"));
    let lids: u32 = env::var("NW_LIDS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);

    if lids & NW_LID_NEWNET != 0 {
        lid_netns();
    }
    if lids & NW_LID_NEWNS != 0 {
        lid_newns();
    }
    if lids & NW_LID_SECCOMP != 0 {
        lid_seccomp();
    }

    let c_path = CString::new(path).unwrap_or_else(|_| die("path nul"));
    let c_name = CString::new(name).unwrap_or_else(|_| die("name nul"));
    let argv = [c_name.as_ptr(), std::ptr::null()];
    unsafe {
        execv(c_path.as_ptr(), argv.as_ptr());
    }
    die("exec house");
}
