// SPDX-License-Identifier: Apache-2.0
// testagent: a throwaway ssh-agent for the local litewitness end-to-end test (the box has no OpenSSH).
// It generates an EPHEMERAL Ed25519 witness key in memory, serves it on a unix socket, and prints
// the SSH fingerprint litewitness wants (-key) and the witness's cosignature/v1 vkey (type 0x04).
// Test use only: the key never touches disk and dies with the process.
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"flag"
	"fmt"
	"log"
	"net"
	"os"

	"golang.org/x/crypto/ssh"
	"golang.org/x/crypto/ssh/agent"
)

func main() {
	sock := flag.String("sock", "agent.sock", "unix socket to serve the agent on")
	name := flag.String("name", "e2e.invalid/witness", "witness name (for the printed vkey)")
	flag.Parse()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		log.Fatal(err)
	}
	kr := agent.NewKeyring()
	if err := kr.Add(agent.AddedKey{PrivateKey: priv, Comment: "e2e witness"}); err != nil {
		log.Fatal(err)
	}
	spub, _ := ssh.NewPublicKey(pub)
	h := sha256.Sum256(append(append([]byte(*name+"\n"), 0x04), pub...))
	vkey := fmt.Sprintf("%s+%s+%s", *name, hex.EncodeToString(h[:4]), base64.StdEncoding.EncodeToString(append([]byte{0x04}, pub...)))
	os.Remove(*sock)
	l, err := net.Listen("unix", *sock)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("FINGERPRINT=%s\nVKEY=%s\n", ssh.FingerprintSHA256(spub), vkey)
	os.Stdout.Sync()
	for {
		c, err := l.Accept()
		if err != nil {
			log.Fatal(err)
		}
		go agent.ServeAgent(kr, c)
	}
}
