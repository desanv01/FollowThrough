//! Owned fake Docker helper for synthetic executor tests only.
//!
//! It never invokes Docker, a container, a shell, candidate Python or any provider.
//! It records its argv/environment into `transcript.txt` beside its executable and
//! follows `mode.txt`:
//!   sleep  - print a line and sleep for ten seconds (timeout/reap probe)
//!   flood  - write 100 KiB to stdout then exit (bounded capture probe)
//!   quick  - print a line and exit zero
//!
//! The test copies this binary into an owned scratch directory and points the
//! production wrapper at it through the synthetic library boundary.

use std::{
    env,
    fs::{self, OpenOptions},
    io::{self, Write},
    path::PathBuf,
    process::{Command, ExitCode},
    thread,
    time::Duration,
};

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn GetStdHandle(kind: u32) -> isize;
    fn CloseHandle(handle: isize) -> i32;
}

fn main() -> ExitCode {
    let first_arg = env::args().nth(1);
    if first_arg.as_deref() == Some("pipe-holder") {
        thread::sleep(Duration::from_millis(650));
        return ExitCode::SUCCESS;
    }
    if first_arg.as_deref() == Some("pipe-parent") {
        let _holder = Command::new(env::current_exe().unwrap())
            .arg("pipe-holder")
            .spawn()
            .unwrap();
        thread::sleep(Duration::from_millis(700));
        return ExitCode::SUCCESS;
    }
    if first_arg.as_deref() == Some("stderr-overflow") {
        #[cfg(windows)]
        unsafe {
            assert_ne!(CloseHandle(GetStdHandle((-11i32) as u32)), 0);
        }
        thread::sleep(Duration::from_millis(60));
        let mut stderr = io::stderr().lock();
        stderr.write_all(&[b'x'; 1025]).unwrap();
        stderr.flush().unwrap();
        thread::sleep(Duration::from_millis(150));
        return ExitCode::SUCCESS;
    }
    let exe = env::current_exe().unwrap_or_else(|_| PathBuf::from("fake-docker.exe"));
    let directory = exe.parent().unwrap_or_else(|| std::path::Path::new("."));
    let mode = fs::read_to_string(directory.join("mode.txt"))
        .unwrap_or_else(|_| "quick".to_string())
        .trim()
        .to_string();
    let mut transcript = OpenOptions::new()
        .create(true)
        .append(true)
        .open(directory.join("transcript.txt"))
        .ok();
    if let Some(file) = transcript.as_mut() {
        let _ = writeln!(file, "MODE\t{mode}");
        for (index, arg) in env::args().skip(1).enumerate() {
            let _ = writeln!(file, "ARG\t{index}\t{arg}");
        }
        let mut keys: Vec<String> = env::vars().map(|(key, _)| key).collect();
        keys.sort();
        for key in keys {
            let value = env::var(&key).unwrap_or_default();
            let _ = writeln!(file, "ENV\t{key}\t{value}");
        }
        let _ = file.flush();
    }
    match mode.as_str() {
        "sleep" => {
            println!("fake docker started");
            let _ = std::io::stdout().flush();
            thread::sleep(Duration::from_secs(10));
            ExitCode::SUCCESS
        }
        "flood" => {
            let chunk = vec![b'x'; 1024];
            let mut stdout = std::io::stdout().lock();
            for _ in 0..100 {
                if stdout.write_all(&chunk).is_err() {
                    break;
                }
            }
            let _ = stdout.flush();
            thread::sleep(Duration::from_millis(100));
            ExitCode::SUCCESS
        }
        _ => {
            println!("fake docker quick");
            ExitCode::SUCCESS
        }
    }
}
