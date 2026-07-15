function good(): void {
  logger.info("x"); // clean: logger not console
}

function bad(): void {
  console.log("x"); // anti: raw console.log
}
