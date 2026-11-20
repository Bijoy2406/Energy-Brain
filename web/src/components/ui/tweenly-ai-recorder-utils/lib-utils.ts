import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

/** shadcn's class-name helper: join conditional classes and let the last Tailwind class win. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
