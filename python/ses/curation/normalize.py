import re
from typing import List
from pydantic import BaseModel

class NormalizedSection(BaseModel):
    section_id: str
    heading: str
    anchor: str
    text: str

def slugify(text: str) -> str:
    # 1. Convert to lowercase
    s = text.lower()
    # 2. Remove special characters (anything not lowercase letters, digits, whitespace, or hyphens)
    s = re.sub(r'[^a-z0-9\s\-]', '', s)
    # 3. Replace spaces/hyphens with a single hyphen
    s = re.sub(r'[\s\-]+', '-', s)
    # 4. Strip leading/trailing hyphens
    s = s.strip('-')
    return s

def normalize_markdown(content: str, citation_base: str) -> List[NormalizedSection]:
    # Normalize line endings to ensure platform independence
    content = content.replace("\r\n", "\n")
    lines = content.split("\n")
    
    sections: List[NormalizedSection] = []
    seen_slugs = {}
    
    in_code_block = False
    opening_backticks_count = 0
    
    current_heading = None
    current_section_id = None
    current_lines = []
    
    def flush_section():
        nonlocal current_heading, current_section_id, current_lines
        text = "\n".join(current_lines).strip()
        
        # If this is the preamble
        if current_heading is None:
            if text:
                sections.append(NormalizedSection(
                    section_id="",
                    heading="",
                    anchor=citation_base,
                    text=text
                ))
        else:
            anchor = f"{citation_base}#{current_section_id}" if current_section_id else citation_base
            sections.append(NormalizedSection(
                section_id=current_section_id,
                heading=current_heading,
                anchor=anchor,
                text=text
            ))
        current_lines = []

    for line in lines:
        # Track fenced code blocks
        if not in_code_block:
            # Check if opening fence: starts with 0 to 3 spaces of indentation, followed by 3 or more backticks
            open_match = re.match(r'^ {0,3}(`{3,})', line)
            if open_match:
                in_code_block = True
                opening_backticks_count = len(open_match.group(1))
                current_lines.append(line)
                continue
        else:
            # Check if closing fence: starts with 0 to 3 spaces, at least opening_backticks_count backticks, no non-whitespace after
            close_match = re.match(r'^ {0,3}(`{' + str(opening_backticks_count) + r',})\s*$', line)
            if close_match:
                in_code_block = False
                opening_backticks_count = 0
                current_lines.append(line)
                continue
            else:
                current_lines.append(line)
                continue
            
        # Outside a fenced code block, check for headings
        stripped_line = line.strip()
        heading_match = re.match(r'^(#{1,6})\s+(.*)$', stripped_line)
        if heading_match:
            flush_section()
            
            raw_heading = heading_match.group(2).strip()
            # Strip trailing markdown heading hashes if present
            clean_heading = re.sub(r'\s+#+$', '', raw_heading).strip()
            
            base_slug = slugify(clean_heading)
            # If slugification results in empty string, fallback to 'heading'
            if not base_slug:
                base_slug = "heading"
            
            # Header disambiguation
            if base_slug not in seen_slugs:
                seen_slugs[base_slug] = 1
                unique_slug = base_slug
            else:
                count = seen_slugs[base_slug]
                unique_slug = f"{base_slug}-{count}"
                seen_slugs[base_slug] += 1
                
            current_heading = clean_heading
            current_section_id = unique_slug
        else:
            current_lines.append(line)
            
    # Flush any remaining text in the last section
    flush_section()
    
    return sections
