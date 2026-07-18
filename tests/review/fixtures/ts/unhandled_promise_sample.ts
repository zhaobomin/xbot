function good(): void {
  fetch("/x").then((r) => r.json()).catch((e) => report(e)); // clean: has .catch
}

function bad(): void {
  fetch("/x").then((r) => r.json()); // anti: .then without .catch
}
