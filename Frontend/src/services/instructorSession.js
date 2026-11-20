import { createContext, useContext } from 'react'

export const InstructorSessionContext = createContext(null)
export function useInstructorSession() {
  return useContext(InstructorSessionContext)
}
