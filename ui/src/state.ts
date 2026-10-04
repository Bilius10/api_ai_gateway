export class LatestSelection {
  private selected = '';
  private generation = 0;

  select(id: string): number {
    this.selected = id;
    this.generation += 1;
    return this.generation;
  }

  isCurrent(id: string, token: number): boolean {
    return this.selected === id && this.generation === token;
  }
}
