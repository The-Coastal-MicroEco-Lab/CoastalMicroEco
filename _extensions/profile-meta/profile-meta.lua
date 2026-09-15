return {
  ["profile-photo"] = function(args, kwargs, meta)
  
    if meta.image == nil then
      return pandoc.Null()
    end
  
    local src = pandoc.utils.stringify(meta.image)
  
    if src == "" or src == "false" then
      return pandoc.Null()
    end
  
    local alt = "Profile photo"
  
    if meta.title ~= nil then
      alt = pandoc.utils.stringify(meta.title)
    end
  
    local image = pandoc.Image(
      { pandoc.Str(alt) },
      src,
      "",
      pandoc.Attr("", {"profile-photo"})
    )
  
    return pandoc.Div(
      { pandoc.Para({image}) },
      pandoc.Attr("", {"profile-photo-wrapper"})
    )
  end,
  
  ["profile-interests"] = function(args, kwargs, meta)
  
    local interests = meta.interests
  
    if interests == nil then
      return pandoc.Null()
    end
  
    local output = {}
  
    for i, interest in ipairs(interests) do
  
      if i > 1 then
        -- comma + non-breaking space
        table.insert(output, pandoc.RawInline("html", ", "))
      end
  
      -- Keep the interest itself exactly as Pandoc already represents it
      local value = pandoc.utils.stringify(interest)
      local parsed = pandoc.read(value, "markdown")
      local block = parsed.blocks[1]
  
      if block and block.content then
        for _, inline in ipairs(block.content) do
          table.insert(output, inline)
        end
      end
  
    end
  
    if #output == 0 then
      return pandoc.Null()
    end
  
    return pandoc.Inlines(output)
  end,
  
    ["profile-education"] = function(args, kwargs, meta)
    
      local education = meta.education
    
      if education == nil then
        return pandoc.Null()
      end
    
      local items = {}
    
      for _, degree in ipairs(education) do
        local value = pandoc.utils.stringify(degree)
    
        table.insert(
          items,
          {
            pandoc.Plain(
              pandoc.read(value, "markdown").blocks[1].content
            )
          }
        )
      end
    
      if #items == 0 then
        return pandoc.Null()
      end
    
      return pandoc.BulletList(items)
    end,

  ["profile-links"] = function(args, kwargs, meta)

    local links = meta.links

    if links == nil then
      return pandoc.Null()
    end

    local items = {}

    for _, link in ipairs(links) do

      local href = pandoc.utils.stringify(link.href or "")
      local text = pandoc.utils.stringify(link.text or link.href or "")

      if href ~= "" then
        table.insert(
          items,
          pandoc.Link(text, href)
        )
      end
    end

    if #items == 0 then
      return pandoc.Null()
    end

    local output = {}

    for i, item in ipairs(items) do
      table.insert(output, item)

      if i < #items then
        table.insert(output, pandoc.Str(" · "))
      end
    end

    return output
  end
}