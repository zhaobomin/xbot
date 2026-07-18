function good(): void {
  clearTimeout(timer);
  timer = setTimeout(reconnect, 1000); // clean: cleared existing timer
}

function bad(): void {
  setTimeout(reconnect, 1000); // anti: no clearTimeout before new timer
}
