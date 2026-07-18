function good(): string {
  let x: string = "ok"; // clean: typed annotation
  return x;
}

function bad(): any {
  let x: any = 1; // anti: untyped any
  return x;
}
